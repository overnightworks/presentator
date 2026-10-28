"""Composition root: builds the adapters, wires the application, serves the API.

Imports every other layer, because it is the one place that decides which
adapter satisfies which port.
"""

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.routing import Host, Router
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.oidc import OidcClientConfig, OidcRelyingParty
from webauth.proxies import TrustedProxies
from webauth.rate_limit import SingleProcessRateLimitBackend

from presentator.adapters.agent_service import ServiceAnswerer
from presentator.adapters.agent_tokens import PersonAccessTokens, WorkloadToken
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
    age_in_words,
    duration_in_words,
    load_catalogs,
)
from presentator.adapters.deck_texts import MirroredDeckTexts
from presentator.adapters.decks import (
    FilesystemLocalMount,
    MirroredConnectionChecker,
    MirroredDeckFolders,
    SourceCredentials,
    SourceMirrors,
    SqliteDeckStore,
    SqliteSourceRunStore,
    SqliteSourceStore,
)
from presentator.adapters.identity import (
    OldDatabaseError,
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
    connection_fingerprint_key,
    secret_box,
    talk_session_secret,
)
from presentator.adapters.sentences import split_sentences
from presentator.adapters.speech import UdsSpeaking, UdsSpeech, UdsSpeechHearing
from presentator.api.auth import (
    InstalledAuth,
    LobbyPrivate,
    content_security_policy,
    create_lobby,
)
from presentator.api.copresenter import CoPresenterSurface
from presentator.api.pages import Pages
from presentator.api.people import AdminPeople
from presentator.api.talk import create_talk, talk_content_security_policy
from presentator.application.copresenter import AskLimiter, CoPresenterUse
from presentator.application.decks import Decks
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL
from presentator.application.people import People
from presentator.application.preferences import Preferences
from presentator.application.voice import VoiceStatusUse
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
    try:
        create_tables(settings.database)
    except OldDatabaseError as refused:
        raise cannot_start(str(refused)) from None
    clock = SystemClock()
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
        settings, identity_writes, relying_party_for
    )
    return Instance(
        lobby=_lobby(
            settings,
            decks,
            sessions,
            lobby_auth,
            _lobby_private(settings, decks, sessions, key_drafts, identity_writes),
        ),
        talk=_talk(settings, decks, sessions, talk_auth, mirrors),
        poller=Schedule(
            chore=decks.refresh,
            interval=timedelta(seconds=settings.source_poll_seconds),
        ),
        session_sweep=_session_sweep(sessions, clock),
    )


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
    )


def _lobby(
    settings: Settings,
    decks: Decks,
    sessions: SqliteSessionRecordStore,
    auth: InstalledAuth,
    private: LobbyPrivate,
) -> FastAPI:
    """The lobby host, with the pages and the private routes it serves."""
    return create_lobby(
        sessions=sessions,
        decks=decks,
        pages=_pages(settings),
        auth=auth,
        private=private,
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
    """What only a signed-in admin may reach at the talk host: the agent service."""
    return CoPresenterSurface(
        use=CoPresenterUse(
            answers=ServiceAnswerer(
                origin=settings.agent_service,
                workload_token=WorkloadToken(auth.relying_party),
            ),
            speech=UdsSpeechHearing(settings.speech_socket),
            decks=decks.store,
            sources=decks.sources,
            texts=MirroredDeckTexts(mirrors=mirrors),
            boundaries=split_sentences,
            speaking=UdsSpeaking(settings.speech_socket),
            person_access=PersonAccessTokens(auth.relying_party, sessions, auth.config),
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
    """What only an admin of the lobby may reach: the host's socket, the people route.

    The people route exists only while a client is named to call it. Its
    accounts delete over the lock the sign-in holds.
    """
    clients = settings.admitted_token_clients()
    return LobbyPrivate(
        voice=VoiceStatusUse(private=UdsSpeech(settings.speech_socket)),
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
        content_security_policy=content_security_policy(),
        talk_origin=settings.talk_origin,
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


def _pages(settings: Settings) -> Pages:
    return Pages(
        preferences=Preferences(
            instance=SqliteInstanceSettingsStore(settings.database),
            people=SqlitePersonPreferencesStore(settings.database),
            catalogs=load_catalogs(CATALOG_DIRECTORY),
        ),
        age_in_words=age_in_words,
        duration_in_words=duration_in_words,
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
    return Router(
        routes=[
            Host(str(urlsplit(settings.public_origin).hostname), instance.lobby),
            Host(str(urlsplit(settings.talk_origin).hostname), instance.talk),
        ],
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
