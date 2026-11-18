"""Composition root: builds the adapters, wires the application, serves the API.

Imports every other layer, because it is the one place that decides which
adapter satisfies which port.
"""

import asyncio
import copy
import logging
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from botocore.exceptions import ConfigParseError
from fastapi import FastAPI
from logline import RequestLineMiddleware
from pydantic import SecretStr
from starlette.routing import Host, Route, Router
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.oidc import OidcClientConfig, OidcRelyingParty
from webauth.ports import RateLimitBackend, SignInTransaction
from webauth.proxies import TrustedProxies

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.build_cache import BuildCache
from presentator.adapters.build_worker import WorkerToolchain
from presentator.adapters.builds import (
    DeckToolchain,
    HostToolchain,
    PackageJsonThemes,
    SlidevBuilds,
)
from presentator.adapters.deck_texts import BuiltDeckTexts
from presentator.adapters.decks import (
    MirroredConnectionChecker,
    MirroredDeckFolders,
    SourceMirrors,
)
from presentator.adapters.identity import SystemClock, TokenIdentifierFactory
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.migrate import (
    MIGRATIONS_DIRECTORY,
    DatabaseRefusedError,
    RefusalReason,
    cap_alembic_logging,
    database_is_ready,
    migration_history,
    serving_refusal,
)
from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.adapters.postgres.refresh_owner import PostgresRefreshOwnership
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import (
    PostgresSourceCredentials,
    PostgresSourceStore,
)
from presentator.adapters.run_store import StoredRuns, object_store_client
from presentator.adapters.secrets import (
    SecretBox,
    TokenWebhookSecrets,
    connection_fingerprint_key,
    secret_box,
    talk_session_secret,
)
from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.adapters.shared_redis import RateLimitedHost, SharedRedis
from presentator.adapters.speech import SpeechHearing, SpeechSpeaking
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.agent.backend import gateway_answerer
from presentator.agent.mcp import install_mcp_front
from presentator.api.auth import (
    BACKCHANNEL_LOGOUT_PATH,
    AgentFront,
    InstalledAuth,
    LobbyPrivate,
    content_security_policy,
    create_lobby,
)
from presentator.api.copresenter import CoPresenterSurface
from presentator.api.health import Readiness, Stopping
from presentator.api.lobby_shell import LobbyBuildError, LobbyShell
from presentator.api.people import AdminPeople
from presentator.api.talk import create_talk, talk_content_security_policy
from presentator.application.copresenter import CoPresenterUse
from presentator.application.decks import Decks
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL
from presentator.application.own_decks import OwnDecks
from presentator.application.people import People
from presentator.application.preferences import Preferences
from presentator.contracts.decks import ToolchainIdentity
from presentator.host.command_line import (
    MIGRATE_COMMAND,
    command_line,
    migrate_the_database,
)
from presentator.host.config import (
    ConfigurationError,
    PostgresDatabase,
    Settings,
    WhereBuildsRun,
    cannot_start,
    load_settings,
    variable_of,
)
from presentator.host.oidc import (
    admission_of,
    client_configs_of,
    relying_party,
)
from presentator.host.polling import ChoreCode, Schedule
from presentator.host.stopping_server import (
    ServerReason,
    StoppingServer,
    configure_logging,
    end_failed,
    served_config,
)
from presentator.host.store_connection import (
    StoreFailure,
    StoreKind,
    reach_redis,
    timed_connection,
)
from presentator.host.worker_serving import emergency_end
from presentator.ports.copresenter import AskLimiter, DeckTexts
from presentator.ports.decks import (
    DeckStore,
    DeployKeyDrafts,
    RefreshOwnership,
    SourceRuns,
    SourceStore,
)
from presentator.ports.identity import IdentifierFactory
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)

_log = logging.getLogger(__name__)

SERVICE_NAME = "presentator"

RelyingPartyFor = Callable[[OidcClientConfig], OidcRelyingParty]


def _nothing_to_close() -> None:
    """What an instance built without stores holds open: nothing."""


# The files boto3 reads the object store's key and configuration from.
_OBJECT_STORE_FILES: Final = ("AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE")
# The wait between taking the lease and the answer's clock starting, so the lease
# outlives the whole answer.
ASK_LEASE_MARGIN_SECONDS: Final = 30


@dataclass(frozen=True, slots=True, kw_only=True)
class Instance:
    """What a running instance is: the addresses, and the schedules beside them."""

    lobby: FastAPI
    talk: FastAPI
    poller: Schedule
    session_sweep: Schedule
    close: Callable[[], None] = _nothing_to_close
    stopping: Stopping = field(default_factory=Stopping)


@dataclass(frozen=True, slots=True, kw_only=True)
class Stores:
    """The adapter behind every store port, all of one database."""

    sources: SourceStore
    key_drafts: DeployKeyDrafts
    deck_store: DeckStore
    source_runs: SourceRuns
    refresh_ownership: RefreshOwnership
    credentials: PostgresSourceCredentials
    sessions: PostgresSessionRecordStore
    identity_writes: PostgresIdentityWrites
    instance_settings: InstanceSettingsStore
    person_preferences: PersonPreferencesStore
    readiness: Readiness
    close: Callable[[], None]


def build_instance(
    settings: Settings,
    relying_party_for: RelyingPartyFor = relying_party,
    migrations_directory: Path = MIGRATIONS_DIRECTORY,
) -> Instance:
    """Choose the adapter behind every port and hand the routes their use cases.

    A Postgres database is served only at the head of the history in
    `migrations_directory` or newer.
    """
    shell = _lobby_shell(settings)
    redis = SharedRedis(settings.redis_url.get_secret_value(), settings.redis_password)
    reach_redis(redis)
    toolchain = _identity_of_the_toolchain(settings)
    stopping = Stopping()
    stores = _with_redis(_stores(settings, migrations_directory, toolchain), redis)
    stores = copy.replace(stores, readiness=stopping.unready(stores.readiness))
    # A refusal after the stores opened releases their connections, which
    # nothing else holds.
    try:
        mirrors = SourceMirrors(
            directory=settings.mirrors,
            credentials=stores.credentials,
            pull_timeout=_source_timeout(settings),
        )
        cache = _build_cache(settings)
        decks = _decks(
            settings, stores, mirrors=mirrors, cache=cache, toolchain=toolchain
        )
        texts = BuiltDeckTexts(cache=cache)
        lobby_auth, talk_auth = installed_auths(
            settings, shell, stores.identity_writes, redis, relying_party_for
        )
        return Instance(
            lobby=_lobby(
                settings,
                stores,
                decks=decks,
                auth=lobby_auth,
                texts=texts,
                shell=shell,
            ),
            talk=_talk(
                settings,
                stores,
                decks,
                talk_auth,
                texts,
                redis=redis,
                stopping=stopping,
            ),
            poller=_poller(settings, decks),
            session_sweep=_session_sweep(stores.sessions, SystemClock()),
            close=stores.close,
            stopping=stopping,
        )
    except BaseException:
        stores.close()
        raise


def _with_redis(stores: Stores, redis: SharedRedis) -> Stores:
    """The stores, ready only while Redis answers too and closed with its client."""
    database_ready = stores.readiness

    def ready() -> bool:
        return database_ready() and redis.is_ready()

    return copy.replace(
        stores,
        readiness=ready,
        close=partial(_closed_in_turn, stores.close, redis.close),
    )


def _stores(
    settings: Settings, migrations_directory: Path, toolchain: ToolchainIdentity
) -> Stores:
    """The stores of the Postgres the settings name."""
    # One box for both directions: what the store writes down is what the
    # resolver opens at the pull, and neither picks its own key.
    box = secret_box(settings.secret_key.get_secret_value())
    identifiers = TokenIdentifierFactory()
    return _postgres_stores(
        settings.postgres,
        box,
        identifiers,
        migrations_directory,
        refresh=(toolchain, build_bound_of(settings)),
    )


def _postgres_stores(
    database: PostgresDatabase,
    box: SecretBox,
    identifiers: IdentifierFactory,
    migrations_directory: Path,
    *,
    refresh: tuple[ToolchainIdentity, timedelta],
) -> Stores:
    """The stores over the one engine this process opens; serve creates no table.

    `refresh` is the toolchain this process announces and the bound of every wait
    for the refresh.
    """
    history = migration_history(migrations_directory)
    engine = create_database_engine(database.url, database.password)
    with timed_connection(StoreKind.POSTGRES) as step:
        refusal = serving_refusal(engine, history)
        if refusal is not None:
            if refusal.reason is RefusalReason.NOT_ANSWERING:
                step.fail(StoreFailure.NOT_ANSWERING)
            else:
                step.refuse(refusal.reason)
    if refusal is not None:
        engine.dispose()
        raise refusal
    toolchain, bound = refresh
    ownership = PostgresRefreshOwnership(
        engine, toolchain=toolchain, lock_timeout=bound
    )
    return Stores(
        sources=PostgresSourceStore(
            engine=engine, identifiers=identifiers, box=box, fence=ownership
        ),
        key_drafts=PostgresSourceKeyDrafts(
            engine=engine, identifiers=identifiers, box=box
        ),
        deck_store=PostgresDeckStore(engine=engine, fence=ownership),
        source_runs=PostgresSourceRunStore(engine=engine, fence=ownership),
        refresh_ownership=ownership,
        credentials=PostgresSourceCredentials(engine=engine, box=box),
        sessions=PostgresSessionRecordStore(engine),
        identity_writes=PostgresIdentityWrites(engine, LoggingAuditSink()),
        instance_settings=PostgresInstanceSettingsStore(engine),
        person_preferences=PostgresPersonPreferencesStore(engine),
        readiness=partial(database_is_ready, engine, history),
        close=partial(_closed_in_turn, ownership.close, engine.dispose),
    )


def _closed_in_turn(first: Callable[[], None], then: Callable[[], None]) -> None:
    """Close the first, and the then even where the first fails."""
    try:
        first()
    finally:
        then()


def _poller(settings: Settings, decks: Decks) -> Schedule:
    """The schedule that takes in what every source carries now."""
    return Schedule(
        chore=decks.refresh,
        chore_code=ChoreCode.SOURCE_POLL,
        interval=timedelta(seconds=settings.source_poll_seconds),
    )


def _lobby(  # noqa: PLR0913 - the composition root hands the lobby each owner
    settings: Settings,
    stores: Stores,
    *,
    decks: Decks,
    auth: InstalledAuth,
    texts: DeckTexts,
    shell: LobbyShell,
) -> FastAPI:
    """The lobby over the use cases, the sign-in wiring and the agent's front."""
    return create_lobby(
        sessions=stores.sessions,
        decks=decks,
        preferences=_preferences(stores),
        auth=auth,
        private=_lobby_private(settings, stores, decks),
        agent=agent_front(
            origin=settings.public_origin,
            issuer=settings.oidc_issuer,
            transaction=auth.transaction,
        ),
        own_decks=OwnDecks(
            decks=decks.store,
            sources=decks.sources,
            texts=texts,
        ),
        shell=shell,
        readiness=stores.readiness,
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
        raise cannot_start(
            str(refused), variables=(variable_of("lobby_build"),)
        ) from None


def _build_cache(settings: Settings) -> BuildCache:
    """The runs on this disk, kept in the store through this process's one client.

    The client reads the platform's key and configuration files as it is made,
    so one that does not parse refuses the start.
    """
    try:
        client = object_store_client()
    except ConfigParseError:
        raise cannot_start(
            "the object store's key or configuration file does not parse",
            variables=_OBJECT_STORE_FILES,
        ) from None
    return BuildCache(
        root=settings.builds,
        store=StoredRuns(client=client, bucket=settings.s3_bucket),
        bound_bytes=settings.build_cache_bytes,
    )


def _source_timeout(settings: Settings) -> timedelta:
    """The one bound a pull and a connection check both take."""
    return timedelta(seconds=settings.source_timeout_seconds)


def _decks(
    settings: Settings,
    stores: Stores,
    *,
    mirrors: SourceMirrors,
    cache: BuildCache,
    toolchain: ToolchainIdentity,
) -> Decks:
    """The deck use cases over the adapters the instance chose."""
    build_bound = build_bound_of(settings)
    return Decks(
        sources=stores.sources,
        key_drafts=stores.key_drafts,
        folders=MirroredDeckFolders(mirrors=mirrors),
        store=stores.deck_store,
        builder=SlidevBuilds(
            cache=cache,
            identity=toolchain,
            toolchain=_what_builds_a_deck(settings, bound=build_bound),
            mirrors=mirrors,
            output_megabytes=settings.build_output_megabytes,
        ),
        source_runs=stores.source_runs,
        # The same bound a scheduled pull takes: Check connection asks the
        # same remote for the same one thing, just without writing it down.
        checker=MirroredConnectionChecker(
            check_timeout=_source_timeout(settings),
            known_hosts=settings.mirrors / "known_hosts",
        ),
        toolchain_themes=PackageJsonThemes(project=settings.toolchain),
        ownership=stores.refresh_ownership,
        poll_interval=timedelta(seconds=settings.source_poll_seconds),
        fingerprint_key=connection_fingerprint_key(
            settings.secret_key.get_secret_value(),
        ),
        clock=SystemClock(),
        webhook_secrets=TokenWebhookSecrets(),
    )


def _talk(  # noqa: PLR0913 - the composition root hands the talk host each owner
    settings: Settings,
    stores: Stores,
    decks: Decks,
    auth: InstalledAuth,
    texts: DeckTexts,
    *,
    redis: SharedRedis,
    stopping: Stopping,
) -> FastAPI:
    """The talk host, with the co-presenter it speaks to."""
    return create_talk(
        sessions=stores.sessions,
        decks=decks,
        auth=auth,
        lobby_origin=settings.public_origin,
        copresenter=_copresenter_surface(
            settings, stores.sessions, decks, auth, texts, redis, stopping
        ),
        readiness=stores.readiness,
    )


def _copresenter_surface(  # noqa: PLR0913, PLR0917 - the composition root hands the surface each owner
    settings: Settings,
    sessions: PostgresSessionRecordStore,
    decks: Decks,
    auth: InstalledAuth,
    texts: DeckTexts,
    redis: SharedRedis,
    stopping: Stopping,
) -> CoPresenterSurface:
    """What only a signed-in admin may reach at the talk host: the gateway's answers."""
    answers = gateway_answerer(settings.agent, auth.relying_party)
    return CoPresenterSurface(
        use=CoPresenterUse(
            answers=answers,
            speech=SpeechHearing(
                settings.speech_service, voices=settings.speech_voices()
            ),
            decks=decks.store,
            sources=decks.sources,
            texts=texts,
            boundaries=split_sentences,
            speaking=SpeechSpeaking(
                settings.speech_service, voices=settings.speech_voices()
            ),
            person_access=SessionAccess(
                auth.relying_party,
                sessions,
                auth.config,
                speech_scope=settings.speech_service_scope,
            ),
        ),
        limiter=ask_limiter_of(redis, deadline_seconds=answers.deadline_seconds),
        browser_origin=settings.talk_origin,
        deadline_seconds=answers.deadline_seconds,
        stopping=stopping,
    )


def ask_limiter_of(redis: SharedRedis, *, deadline_seconds: float) -> AskLimiter:
    """The ask lease: the answer's deadline plus a margin, never renewed."""
    return redis.ask_limiter(lease_seconds=deadline_seconds + ASK_LEASE_MARGIN_SECONDS)


def _lobby_private(settings: Settings, stores: Stores, decks: Decks) -> LobbyPrivate:
    """What only an admin of the lobby may reach: the people route.

    The people route exists only while a client is named to call it. Its
    accounts delete over the lock the sign-in holds.
    """
    clients = settings.admitted_token_clients()
    return LobbyPrivate(
        admin_people=AdminPeople(
            people=People(
                decks=decks,
                accounts=stores.identity_writes,
                sessions=stores.sessions,
                preferences=stores.person_preferences,
                key_drafts=stores.key_drafts,
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
    identity_writes: PostgresIdentityWrites,
    redis: SharedRedis,
    relying_party_for: RelyingPartyFor = relying_party,
) -> tuple[InstalledAuth, InstalledAuth]:
    """The lobby's sign-in wiring and the talk host's, over one identity-writes owner.

    Each host spends its own budgets in the Redis every pod shares.

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
            rate_limits=redis.rate_limits(RateLimitedHost.LOBBY),
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
            rate_limits=redis.rate_limits(RateLimitedHost.TALK),
        ),
        relying_party=relying_party_for(talk_client),
        content_security_policy=talk_content_security_policy(),
    )
    return lobby, talk


def _preferences(stores: Stores) -> Preferences:
    return Preferences(
        instance=stores.instance_settings, people=stores.person_preferences
    )


def _session_sweep(
    sessions: PostgresSessionRecordStore, clock: SystemClock
) -> Schedule:
    """Delete expired sessions on a schedule, reading the clock on every run."""
    return Schedule(
        chore=lambda: sessions.delete_expired(clock.now()),
        chore_code=ChoreCode.SESSION_SWEEP,
        interval=SESSION_SWEEP_INTERVAL,
    )


def _identity_of_the_toolchain(settings: Settings) -> ToolchainIdentity:
    """The identity of this web's own toolchain project, or a refusal to start.

    Computed once, for the worker runner and the host runner alike: both build
    from the project this instance carries.
    """
    try:
        return toolchain_identity_of(settings.toolchain)
    except OSError as unreadable:
        setting = variable_of("toolchain")
        reason = f"{setting}: the project cannot be read: {unreadable}"
        raise cannot_start(reason, variables=(setting,)) from None


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
    """The client of the sealed build workers; no worker has to answer to start."""
    link = settings.build_worker
    return WorkerToolchain(
        host=link.host,
        port=link.port,
        key=link.key.get_secret_value().encode(),
        megabytes=settings.build_output_megabytes,
        reply_bound_seconds=emergency_end(0.0, bound.total_seconds()),
    )


def web_auth_config_for(
    secret: SecretStr,
    *,
    trusted_proxies: str,
    origin: str,
    rate_limits: RateLimitBackend,
) -> WebAuthConfig:
    """The library configuration one origin of this host runs: a 12 hour cap.

    The cookies carry `Secure`, and the library's `__Host-` names: the origin
    is https, and a browser would not send a `Secure` cookie back over plain
    http. The only host this configuration answers to is the one people reach
    that origin under, and `secret` signs its cookies alone.
    """
    lifetime_seconds = int(SESSION_LIFETIME.total_seconds())
    return WebAuthConfig(
        session_secret=secret,
        trusted_proxies=TrustedProxies.parse(trusted_proxies),
        rate_limits=rate_limits,
        allowed_hosts_exact=frozenset({str(urlsplit(origin).hostname)}),
        session_lifetime=SessionLifetime(
            idle_seconds=lifetime_seconds,
            absolute_max_age_seconds=lifetime_seconds,
        ),
        cookie_samesite="lax",
        cookie_secure=True,
    )


def main(
    arguments: Sequence[str] | None = None,
    relying_party_for: RelyingPartyFor = relying_party,
    migrations_directory: Path = MIGRATIONS_DIRECTORY,
) -> None:
    """Migrate the database when asked to, else serve the lobby.

    Both run on the configuration the environment carries; a refused
    configuration or database ends either in one line and exit status 1.
    """
    configure_logging(SERVICE_NAME)
    cap_alembic_logging()
    try:
        if command_line().parse_args(arguments).command == MIGRATE_COMMAND:
            migrate_the_database()
            return
        _log.info("process.started")
        settings = load_settings()
        instance = build_instance(settings, relying_party_for, migrations_directory)
    except DatabaseRefusedError as refused:
        driver_error = refused.driver_error
        codes = {} if driver_error is None else {"error_type": driver_error}
        end_failed(refused.reason, **codes)
    except ConfigurationError as refused:
        # The settings by their variables; the message may name a path.
        end_failed(
            ServerReason.CONFIGURATION_REFUSED, setting=",".join(refused.variables)
        )
    asyncio.run(_serve(instance, settings))


def served_by_host(instance: Instance, settings: Settings) -> RequestLineMiddleware:
    """The app that answers each hostname, and one route for any other.

    The `Host` header alone chooses: a request for a hostname this instance does
    not serve, with a port or without, or with none, gets the router's 404 and a
    socket is refused, so neither app ever answers under the other's name. The
    one exception is the lobby's back-channel logout path: the identity provider
    reaches it under the name of the network it shares with the instance, and
    the signed Logout Token is its only control. Other methods on that path
    answer 405. The talk hostname stays the talk app's alone, so the path is a
    404 there. Nothing the
    caller sent builds a redirect.
    """

    @asynccontextmanager
    async def lifespan(_: Router) -> AsyncGenerator[None]:
        # Starlette runs only this router's lifespan, never a `Host` sub-app's, so
        # the lobby's own is entered here.
        async with instance.lobby.router.lifespan_context(instance.lobby):
            yield

    return RequestLineMiddleware(
        Router(
            routes=[
                Host(str(urlsplit(settings.public_origin).hostname), instance.lobby),
                Host(str(urlsplit(settings.talk_origin).hostname), instance.talk),
                Route(BACKCHANNEL_LOGOUT_PATH, instance.lobby, methods=["POST"]),
            ],
            redirect_slashes=False,
            lifespan=lifespan,
        )
    )


async def _serve(instance: Instance, settings: Settings) -> None:
    """Poll and sweep for as long as the server answers, and stop with it."""
    served = served_config(
        served_by_host(instance, settings), host=settings.host, port=settings.port
    )
    server = StoppingServer(served, instance.stopping)
    try:
        async with instance.poller.polling(), instance.session_sweep.polling():
            await server.serve()
    finally:
        instance.close()
