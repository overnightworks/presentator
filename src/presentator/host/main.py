"""Composition root: builds the adapters, wires the application, serves the API.

Imports every other layer, because it is the one place that decides which
adapter satisfies which port.
"""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.routing import Host, Router
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.oidc import OidcClientConfig, OidcRelyingParty
from webauth.ports import SignInTransaction
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.build_worker import WorkerToolchain
from presentator.adapters.builds import (
    DeckToolchain,
    HostToolchain,
    PackageJsonThemes,
    SlidevBuilds,
)
from presentator.adapters.catalog import (
    CATALOG_DIRECTORY,
    load_catalogs,
)
from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.deck_texts import MirroredDeckTexts
from presentator.adapters.decks import (
    FilesystemLocalMount,
    MirroredConnectionChecker,
    MirroredDeckFolders,
    SourceCredentials,
    SourceMirrors,
    SqliteSourceRunStore,
    SqliteSourceStore,
)
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SystemClock,
    TokenIdentifierFactory,
)
from presentator.adapters.key_drafts import SqliteSourceKeyDrafts
from presentator.adapters.preferences import (
    SqliteInstanceSettingsStore,
    SqlitePersonPreferencesStore,
)
from presentator.adapters.schema import create_tables
from presentator.adapters.secrets import (
    TokenWebhookSecrets,
    connection_fingerprint_key,
    secret_box,
    talk_session_secret,
)
from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.adapters.speech import (
    SpeechHearing,
    SpeechSpeaking,
    silence_transport_traces,
)
from presentator.agent.backend import gateway_answerer
from presentator.agent.mcp import (
    install_mcp_front,
)
from presentator.api.auth import (
    AgentFront,
    InstalledAuth,
    LobbyPrivate,
    content_security_policy,
    create_lobby,
)
from presentator.api.copresenter import CoPresenterSurface
from presentator.api.lobby_shell import LobbyBuildError, LobbyShell
from presentator.api.people import AdminPeople
from presentator.api.talk import create_talk, talk_content_security_policy
from presentator.application.copresenter import AskLimiter, CoPresenterUse
from presentator.application.decks import Decks
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL
from presentator.application.own_decks import OwnDecks
from presentator.application.people import People
from presentator.application.preferences import Preferences
from presentator.host.config import (
    Settings,
    WhereBuildsRun,
    cannot_start,
    load_settings,
)
from presentator.host.oidc import (
    admission_of,
    client_configs_of,
    relying_party,
)
from presentator.host.polling import Schedule
from presentator.host.worker_serving import emergency_end

_WORKER_NOT_ANSWERING = (
    "The build worker did not answer within {window:g} seconds. Check that the"
    " build-worker service is running and shares the socket directory with this"
    " service."
)
WAITING_FOR_THE_WORKER = (
    "Waiting for the build worker to answer on %s, for up to %g seconds"
)

_log = logging.getLogger(__name__)

RelyingPartyFor = Callable[[OidcClientConfig], OidcRelyingParty]


@dataclass(frozen=True, slots=True, kw_only=True)
class Instance:
    """What a running instance is: the addresses, and the schedules beside them."""

    lobby: FastAPI
    talk: FastAPI
    poller: Schedule
    session_sweep: Schedule


def build_instance(
    settings: Settings,
    relying_party_for: RelyingPartyFor = relying_party,
) -> Instance:
    """Choose the adapter behind every port and hand the routes their use cases."""
    create_tables(settings.database)
    shell = _lobby_shell(settings)
    # One box for both directions: what the store writes down is what the
    # resolver opens at the pull, and neither picks its own key.
    box = secret_box(settings.secret_key.get_secret_value())
    identifiers = TokenIdentifierFactory()
    sources = SqliteSourceStore(
        database=settings.database,
        identifiers=identifiers,
        box=box,
    )
    key_drafts = SqliteSourceKeyDrafts(
        database=settings.database,
        identifiers=identifiers,
        box=box,
    )
    # The one mounted directory a file-kind source's address may resolve
    # under, real filesystem and all: shared by every use, since a symlink or
    # a remount between them must be caught the same way each time.
    local_mount = FilesystemLocalMount(mount=settings.local_sources_mount)
    mirrors = SourceMirrors(
        directory=settings.mirrors,
        credentials=SourceCredentials(database=settings.database, box=box),
        pull_timeout=_source_timeout(settings),
        local_mount=local_mount,
    )
    decks = _decks(
        settings,
        sources=sources,
        key_drafts=key_drafts,
        mirrors=mirrors,
        local_mount=local_mount,
    )
    sessions = SqliteSessionRecordStore(settings.database)
    # One use case object serves both callers, so the hook and the poll share
    # the one refresh that runs at a time.
    identity_writes = SqliteIdentityWrites(
        settings.database, ProcessWriteLock(), LoggingAuditSink()
    )
    lobby_auth, talk_auth = installed_auths(
        settings, shell, identity_writes, relying_party_for
    )
    return Instance(
        lobby=_lobby(
            settings,
            decks=decks,
            sessions=sessions,
            key_drafts=key_drafts,
            identity_writes=identity_writes,
            auth=lobby_auth,
            mirrors=mirrors,
            shell=shell,
        ),
        talk=_talk(settings, decks, sessions, talk_auth, mirrors),
        poller=_poller(settings, decks),
        session_sweep=_session_sweep(sessions, SystemClock()),
    )


def _poller(settings: Settings, decks: Decks) -> Schedule:
    """The schedule that takes in what every source carries now."""
    return Schedule(
        chore=decks.refresh,
        interval=timedelta(seconds=settings.source_poll_seconds),
    )


def _lobby(  # noqa: PLR0913 - the composition root hands the lobby each owner
    settings: Settings,
    *,
    decks: Decks,
    sessions: SqliteSessionRecordStore,
    key_drafts: SqliteSourceKeyDrafts,
    identity_writes: SqliteIdentityWrites,
    auth: InstalledAuth,
    mirrors: SourceMirrors,
    shell: LobbyShell,
) -> FastAPI:
    """The lobby over the use cases, the sign-in wiring and the agent's front."""
    return create_lobby(
        sessions=sessions,
        decks=decks,
        preferences=_preferences(settings),
        auth=auth,
        private=_lobby_private(settings, decks, sessions, key_drafts, identity_writes),
        agent=agent_front(
            origin=settings.public_origin,
            issuer=settings.oidc_issuer,
            transaction=auth.transaction,
        ),
        own_decks=OwnDecks(
            decks=decks.store,
            sources=decks.sources,
            texts=MirroredDeckTexts(mirrors=mirrors),
        ),
        shell=shell,
    )


def agent_front(
    *,
    origin: str,
    issuer: str,
    transaction: SignInTransaction,
) -> AgentFront:
    """The MCP front, installed over the lobby's routes once they are in."""
    return AgentFront(
        install_resource=partial(
            install_mcp_front,
            origin=origin,
            issuer=issuer,
            transaction=transaction,
        ),
    )


def _lobby_shell(settings: Settings) -> LobbyShell:
    """The lobby app's build, or a refusal to start without one it can serve."""
    try:
        return LobbyShell.read(settings.lobby_build)
    except LobbyBuildError as refused:
        raise cannot_start(str(refused)) from None


def _source_timeout(settings: Settings) -> timedelta:
    """The one bound a pull and a connection check both take."""
    return timedelta(seconds=settings.source_timeout_seconds)


def _decks(
    settings: Settings,
    *,
    sources: SqliteSourceStore,
    key_drafts: SqliteSourceKeyDrafts,
    mirrors: SourceMirrors,
    local_mount: FilesystemLocalMount,
) -> Decks:
    """The deck use cases over the adapters the instance chose."""
    build_bound = build_bound_of(settings)
    return Decks(
        sources=sources,
        key_drafts=key_drafts,
        folders=MirroredDeckFolders(mirrors=mirrors),
        store=SqliteDeckStore(database=settings.database),
        builder=SlidevBuilds(
            builds=settings.builds,
            toolchain=_what_builds_a_deck(settings, bound=build_bound),
            mirrors=mirrors,
            output_megabytes=settings.build_output_megabytes,
        ),
        source_runs=SqliteSourceRunStore(database=settings.database),
        # The same bound a scheduled pull takes: Check connection asks the
        # same remote for the same one thing, just without writing it down.
        checker=MirroredConnectionChecker(
            check_timeout=_source_timeout(settings),
            local_mount=local_mount,
            known_hosts=settings.mirrors / "known_hosts",
        ),
        toolchain_themes=PackageJsonThemes(project=settings.toolchain),
        # One toolchain step's bound, which is what the refresh needs: it never
        # reads a live build, only what a process that is gone left behind.
        build_bound=build_bound,
        fingerprint_key=connection_fingerprint_key(
            settings.secret_key.get_secret_value(),
        ),
        clock=SystemClock(),
        local_mount=local_mount,
        webhook_secrets=TokenWebhookSecrets(),
    )


def _talk(
    settings: Settings,
    decks: Decks,
    sessions: SqliteSessionRecordStore,
    auth: InstalledAuth,
    mirrors: SourceMirrors,
) -> FastAPI:
    """The talk host, with the co-presenter it speaks to."""
    return create_talk(
        sessions=sessions,
        decks=decks,
        auth=auth,
        lobby_origin=settings.public_origin,
        copresenter=_copresenter_surface(settings, sessions, decks, auth, mirrors),
    )


def _copresenter_surface(
    settings: Settings,
    sessions: SqliteSessionRecordStore,
    decks: Decks,
    auth: InstalledAuth,
    mirrors: SourceMirrors,
) -> CoPresenterSurface:
    """What only a signed-in admin may reach at the talk host: the gateway's answers."""
    silence_transport_traces()
    return CoPresenterSurface(
        use=CoPresenterUse(
            answers=gateway_answerer(settings.agent, auth.relying_party),
            speech=SpeechHearing(settings.speech_service, voice=settings.speech_voice),
            decks=decks.store,
            sources=decks.sources,
            texts=MirroredDeckTexts(mirrors=mirrors),
            boundaries=split_sentences,
            speaking=SpeechSpeaking(
                settings.speech_service, voice=settings.speech_voice
            ),
            person_access=SessionAccess(
                auth.relying_party,
                sessions,
                auth.config,
                speech_scope=settings.speech_service_scope,
            ),
        ),
        limiter=AskLimiter(clock=SystemClock()),
        browser_origin=settings.talk_origin,
    )


def _lobby_private(
    settings: Settings,
    decks: Decks,
    sessions: SqliteSessionRecordStore,
    key_drafts: SqliteSourceKeyDrafts,
    identity_writes: SqliteIdentityWrites,
) -> LobbyPrivate:
    """What only an admin of the lobby may reach: the people route.

    The people route exists only while a client is named to call it. Its
    accounts delete over the lock the sign-in holds.
    """
    clients = settings.admitted_token_clients()
    return LobbyPrivate(
        admin_people=AdminPeople(
            people=People(
                decks=decks,
                accounts=identity_writes,
                sessions=sessions,
                preferences=SqlitePersonPreferencesStore(settings.database),
                key_drafts=key_drafts,
            ),
            admitted_clients=clients,
            clock=SystemClock().now,
        )
        if clients
        else None,
    )


def installed_auths(
    settings: Settings,
    shell: LobbyShell,
    identity_writes: SqliteIdentityWrites,
    relying_party_for: RelyingPartyFor = relying_party,
) -> tuple[InstalledAuth, InstalledAuth]:
    """The lobby's sign-in wiring and the talk host's, over one identity-writes owner.

    Each host has its own relying party and its own signing key, so neither
    accepts the other's cookies or pending logins; both write the one
    `sessions` table, so they enter the one transaction under its one write
    lock, which also reports to the one audit sink.
    """
    lobby_client, talk_client = client_configs_of(settings)
    lobby = InstalledAuth(
        config=web_auth_config_for(
            settings.secret_key,
            trusted_proxies=settings.trusted_proxies,
            origin=settings.public_origin,
        ),
        relying_party=relying_party_for(lobby_client),
        admission=admission_of(settings),
        transaction=identity_writes,
        content_security_policy=content_security_policy(shell),
        talk_origin=settings.talk_origin,
        issuer=settings.oidc_issuer,
    )
    talk = replace(
        lobby,
        config=web_auth_config_for(
            SecretStr(talk_session_secret(settings.secret_key.get_secret_value())),
            trusted_proxies=settings.trusted_proxies,
            origin=settings.talk_origin,
        ),
        relying_party=relying_party_for(talk_client),
        content_security_policy=talk_content_security_policy(),
    )
    return lobby, talk


def _preferences(settings: Settings) -> Preferences:
    return Preferences(
        instance=SqliteInstanceSettingsStore(settings.database),
        people=SqlitePersonPreferencesStore(settings.database),
        catalogs=load_catalogs(CATALOG_DIRECTORY),
    )


def _session_sweep(sessions: SqliteSessionRecordStore, clock: SystemClock) -> Schedule:
    """Delete expired sessions on a schedule, reading the clock on every run."""
    return Schedule(
        chore=lambda: sessions.delete_expired(clock.now()),
        interval=SESSION_SWEEP_INTERVAL,
    )


def _what_builds_a_deck(settings: Settings, *, bound: timedelta) -> DeckToolchain:
    """The sealed build worker, or this machine's own toolchain project.

    A deck is code (line 14a), so a build runs in the worker unless this
    deployment asked in as many words for the development run.
    """
    if settings.build_runner is WhereBuildsRun.HOST:
        return HostToolchain(project=settings.toolchain, bound=bound)
    return the_build_worker(settings, bound=bound)


def build_bound_of(settings: Settings) -> timedelta:
    """The one bound of a build step, shared by every runner and the probe."""
    return timedelta(seconds=settings.build_timeout_seconds)


def the_build_worker(settings: Settings, *, bound: timedelta) -> WorkerToolchain:
    """The sealed build worker, which has to answer before anything is served."""
    toolchain = WorkerToolchain(
        socket_path=settings.build_worker_socket,
        megabytes=settings.build_output_megabytes,
        reply_bound_seconds=emergency_end(0.0, bound.total_seconds()),
        expected_uid=0,
        expected_gid=os.getegid(),
    )
    _log.info(
        WAITING_FOR_THE_WORKER,
        settings.build_worker_socket,
        toolchain.connect_window_seconds,
    )
    if not toolchain.is_answering():
        raise cannot_start(
            _WORKER_NOT_ANSWERING.format(window=toolchain.connect_window_seconds)
        )
    return toolchain


def web_auth_config_for(
    secret: SecretStr,
    *,
    trusted_proxies: str,
    origin: str,
) -> WebAuthConfig:
    """The library configuration one origin of this host runs: no Redis, a 12 hour cap.

    The cookies carry `Secure`, and the library's `__Host-` names: the origin
    is https, and a browser would not send a `Secure` cookie back over plain
    http. The only host this configuration answers to is the one people reach
    that origin under, and `secret` signs its cookies alone.
    """
    lifetime_seconds = int(SESSION_LIFETIME.total_seconds())
    return WebAuthConfig(
        session_secret=secret,
        trusted_proxies=TrustedProxies.parse(trusted_proxies),
        rate_limits=SingleProcessRateLimitBackend(),
        allowed_hosts_exact=frozenset({str(urlsplit(origin).hostname)}),
        session_lifetime=SessionLifetime(
            idle_seconds=lifetime_seconds,
            absolute_max_age_seconds=lifetime_seconds,
        ),
        cookie_samesite="lax",
        cookie_secure=True,
    )


def main() -> None:
    """Serve the lobby with the configuration the environment carries."""
    # The root logger drops INFO unless configured, and with it the sign-in
    # audit lines; uvicorn disables propagation only for its own loggers.
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()
    asyncio.run(_serve(build_instance(settings), settings))


def served_by_host(instance: Instance, settings: Settings) -> Router:
    """The app that answers each hostname, and nothing for any other.

    The `Host` header alone chooses: a request for a hostname this instance does
    not serve, with a port or without, gets the router's 404 and a socket is
    refused, so neither app ever answers under the other's name.
    """

    @asynccontextmanager
    async def lifespan(_: Router) -> AsyncGenerator[None]:
        # Starlette runs only this router's lifespan, never a `Host` sub-app's, so
        # the lobby's own is entered here.
        async with instance.lobby.router.lifespan_context(instance.lobby):
            yield

    return Router(
        routes=[
            Host(str(urlsplit(settings.public_origin).hostname), instance.lobby),
            Host(str(urlsplit(settings.talk_origin).hostname), instance.talk),
        ],
        lifespan=lifespan,
    )


async def _serve(instance: Instance, settings: Settings) -> None:
    """Poll and sweep for as long as the server answers, and stop with it."""
    server = uvicorn.Server(
        uvicorn.Config(
            served_by_host(instance, settings),
            host=settings.host,
            port=settings.port,
            proxy_headers=False,
            # A path under /api/admin/ carries a subject, and a rejected WebSocket
            # handshake logs its path as an INFO record of uvicorn.error.
            access_log=False,
            log_level=logging.WARNING,
        ),
    )
    async with instance.poller.polling(), instance.session_sweep.polling():
        await server.serve()
