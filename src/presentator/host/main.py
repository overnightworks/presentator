"""Composition root: builds the adapters, wires the application, serves the API.

Imports every other layer, because it is the one place that decides which
adapter satisfies which port.
"""

import asyncio
import copy
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from botocore.exceptions import ConfigParseError
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.routing import Host, Route, Router
from starlette.types import ASGIApp
from webauth.config import SessionLifetime, WebAuthConfig
from webauth.ports import RateLimitBackend, SessionRecordStore, SignInTransaction
from webauth.proxies import TrustedProxies
from webauth.web_auth import IssuerCalls, WebAuth, web_auth_front

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.build_cache import BuildCache
from presentator.adapters.builds import (
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
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.migrate import (
    MIGRATIONS_DIRECTORY,
    DatabaseRefusedError,
    RefusalReason,
    StoreFault,
    cap_alembic_logging,
    database_is_ready,
    migration_history,
    serving_refusal,
    store_refusal,
)
from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.adapters.postgres.refresh_owner import PostgresRefreshOwnership
from presentator.adapters.postgres.sessions import PostgresSessions
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import (
    PostgresSourceCredentials,
    PostgresSourceStore,
)
from presentator.adapters.run_store import StoredRuns, object_store_client
from presentator.adapters.secrets import (
    TokenWebhookSecrets,
    connection_fingerprint_key,
    secret_box,
    talk_session_secret,
)
from presentator.adapters.shared_redis import RateLimitedHost, SharedRedis
from presentator.agent.mcp import MCP_ENDPOINT_PATH, install_mcp_front
from presentator.api.auth import (
    BACKCHANNEL_LOGOUT_PATH,
    AgentFront,
    LobbyPrivate,
    create_lobby,
    lobby_web_auth,
)
from presentator.api.health import Readiness, Stopping
from presentator.api.lobby_shell import LobbyBuildError, LobbyShell
from presentator.api.people import AdminPeople
from presentator.api.talk import create_talk, talk_web_auth
from presentator.application.decks import Decks
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL
from presentator.application.own_decks import OwnDecks
from presentator.application.people import People
from presentator.application.preferences import Preferences
from presentator.contracts.decks import ToolchainIdentity
from presentator.host.command_line import (
    MIGRATE_COMMAND,
    UNPIN_HOST_COMMAND,
    migrate_the_database,
    read_the_command_line,
    unpin_the_host,
)
from presentator.host.config import Settings, load_settings
from presentator.host.copresenter_surface import copresenter_surface
from presentator.host.deck_toolchain import (
    build_bound_of,
    identity_of_the_toolchain,
    what_builds_a_deck,
)
from presentator.host.environment import ConfigurationError, cannot_start, variable_of
from presentator.host.lifecycle import (
    ServerReason,
    StopLine,
    end_failed,
    signals_end_as_a_stop,
)
from presentator.host.oidc import admission_of, client_configs_of
from presentator.host.polling import ChoreCode, Schedule
from presentator.host.stopping_server import (
    StoppingServer,
    configure_logging,
    served_config,
)
from presentator.host.store_connection import (
    StoreFailure,
    StoreKind,
    reach_redis,
    timed_connection,
)
from presentator.ports.copresenter import DeckTexts
from presentator.ports.decks import (
    DeckStore,
    DeployKeyDrafts,
    RefreshOwnership,
    SourceRuns,
    SourceStore,
)
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)

_log = logging.getLogger(__name__)

SERVICE_NAME = "presentator"


def _nothing_to_close() -> None:
    """What an instance built without stores holds open: nothing."""


async def _nothing_to_aclose() -> None:
    """What an instance built without Redis holds open on the event loop: nothing."""


# The files boto3 reads the object store's key and configuration from.
_OBJECT_STORE_FILES: Final = ("AWS_SHARED_CREDENTIALS_FILE", "AWS_CONFIG_FILE")


@dataclass(frozen=True, slots=True, kw_only=True)
class Instance:
    """What a running instance is: the addresses, and the schedules beside them."""

    lobby: FastAPI
    talk: FastAPI
    trusted_proxies: TrustedProxies
    poller: Schedule
    session_sweep: Schedule
    close: Callable[[], None] = _nothing_to_close
    aclose: Callable[[], Awaitable[None]] = _nothing_to_aclose
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
    host_keys: PostgresHostKeyPins
    sessions: PostgresSessions
    identity_writes: PostgresIdentityWrites
    instance_settings: InstanceSettingsStore
    person_preferences: PersonPreferencesStore
    readiness: Readiness
    close: Callable[[], None]


def build_instance(
    settings: Settings,
    issuer_calls: IssuerCalls | None = None,
    migrations_directory: Path = MIGRATIONS_DIRECTORY,
) -> Instance:
    """Choose the adapter behind every port and hand the routes their use cases.

    A Postgres database is served only at the head of the history in
    `migrations_directory` or newer.
    """
    shell = _lobby_shell(settings)
    redis = SharedRedis(settings.redis_url.get_secret_value(), settings.redis_password)
    reach_redis(redis)
    toolchain = identity_of_the_toolchain(settings)
    stopping = Stopping()
    stores = _with_redis(_stores(settings, migrations_directory, toolchain), redis)
    stores = copy.replace(stores, readiness=stopping.unready(stores.readiness))
    # A refusal after the stores opened releases their connections, which
    # nothing else holds.
    try:
        return _instance_over(
            settings,
            stores,
            redis=redis,
            shell=shell,
            toolchain=toolchain,
            stopping=stopping,
            issuer_calls=issuer_calls,
        )
    except BaseException:
        stores.close()
        raise


def _instance_over(  # noqa: PLR0913 - the composition root hands the instance each owner
    settings: Settings,
    stores: Stores,
    *,
    redis: SharedRedis,
    shell: LobbyShell,
    toolchain: ToolchainIdentity,
    stopping: Stopping,
    issuer_calls: IssuerCalls | None,
) -> Instance:
    """The two hosts and the schedules beside them, over the opened stores."""
    mirrors = _source_mirrors(settings, stores)
    cache = _build_cache(settings)
    decks = _decks(settings, stores, mirrors=mirrors, cache=cache, toolchain=toolchain)
    texts = BuiltDeckTexts(cache=cache)
    preferences = _preferences(stores)
    lobby_auth, talk_auth = built_auths(
        settings,
        sign_in=stores.identity_writes,
        sessions=stores.sessions,
        preferences=preferences,
        shell=shell,
        redis=redis,
        issuer_calls=issuer_calls,
    )
    trusted_proxies = TrustedProxies.parse(settings.trusted_proxies)
    return Instance(
        lobby=_lobby(
            settings,
            stores,
            decks=decks,
            web_auth=lobby_auth,
            preferences=preferences,
            texts=texts,
            shell=shell,
            trusted_proxies=trusted_proxies,
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
        trusted_proxies=trusted_proxies,
        poller=_poller(settings, decks),
        session_sweep=_session_sweep(stores.sessions, SystemClock()),
        close=stores.close,
        aclose=redis.aclose,
        stopping=stopping,
    )


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


def _source_mirrors(settings: Settings, stores: Stores) -> SourceMirrors:
    """This process's mirrors, trusting the host keys the database pins."""
    return SourceMirrors(
        directory=settings.mirrors,
        credentials=stores.credentials,
        pull_timeout=_source_timeout(settings),
        host_keys=stores.host_keys,
    )


def _stores(
    settings: Settings, migrations_directory: Path, toolchain: ToolchainIdentity
) -> Stores:
    """The stores over the one engine this process opens; serve creates no table.

    Every statement on that engine ends within the settings' statement timeout;
    every wait for the refresh, within the build bound.
    """
    # One box for both directions: what the store writes down is what the
    # resolver opens at the pull, and neither picks its own key.
    box = secret_box(settings.secret_key.get_secret_value())
    identifiers = TokenIdentifierFactory()
    statement_timeout = timedelta(seconds=settings.database_statement_timeout_seconds)
    history = migration_history(migrations_directory)
    database = settings.postgres
    engine = create_database_engine(
        database.url, database.password, statement_timeout=statement_timeout
    )
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
    ownership = PostgresRefreshOwnership(
        engine,
        toolchain=toolchain,
        lock_timeout=build_bound_of(settings),
        statement_timeout=statement_timeout,
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
        host_keys=PostgresHostKeyPins(engine=engine),
        sessions=PostgresSessions(engine),
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
    web_auth: WebAuth,
    preferences: Preferences,
    texts: DeckTexts,
    shell: LobbyShell,
    trusted_proxies: TrustedProxies,
) -> FastAPI:
    """The lobby over the use cases, the sign-in wiring and the agent's front."""
    return create_lobby(
        sessions=stores.sessions,
        decks=decks,
        preferences=preferences,
        web_auth=web_auth,
        talk_origin=settings.talk_origin,
        private=_lobby_private(settings, stores, decks),
        agent=agent_front(
            origin=settings.public_origin,
            issuer=settings.oidc_issuer,
            trusted_proxies=trusted_proxies,
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
    *, origin: str, issuer: str, trusted_proxies: TrustedProxies
) -> AgentFront:
    """The MCP front, installed over the lobby's routes once they are in."""
    return AgentFront(
        install_resource=partial(
            install_mcp_front,
            origin=origin,
            issuer=issuer,
            trusted_proxies=trusted_proxies,
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
    so one that does not parse refuses the start. A copy a fetch left half
    written when an earlier process died goes before any request is served.
    """
    try:
        client = object_store_client()
    except ConfigParseError:
        unparsable = "the object store's key or configuration file does not parse"
        raise cannot_start(unparsable, variables=_OBJECT_STORE_FILES) from None
    cache = BuildCache(
        root=settings.builds,
        store=StoredRuns(client=client, bucket=settings.s3_bucket),
        bound_bytes=settings.build_cache_bytes,
    )
    cache.drop_unfinished_fetches()
    return cache


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
            toolchain=what_builds_a_deck(settings, bound=build_bound),
            mirrors=mirrors,
            output_megabytes=settings.build_output_megabytes,
        ),
        source_runs=stores.source_runs,
        # The same bound a scheduled pull takes: Check connection asks the
        # same remote for the same one thing, just without writing it down.
        checker=MirroredConnectionChecker(
            check_timeout=_source_timeout(settings),
            known_hosts=mirrors.known_hosts,
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
    web_auth: WebAuth,
    texts: DeckTexts,
    *,
    redis: SharedRedis,
    stopping: Stopping,
) -> FastAPI:
    """The talk host, with the co-presenter it speaks to."""
    return create_talk(
        sessions=stores.sessions,
        decks=decks,
        web_auth=web_auth,
        lobby_origin=settings.public_origin,
        copresenter=copresenter_surface(
            settings, stores.sessions, decks, web_auth, texts, redis, stopping
        ),
        readiness=stores.readiness,
    )


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


def built_auths(  # noqa: PLR0913 - the composition root hands the builders each owner
    settings: Settings,
    *,
    sign_in: SignInTransaction,
    sessions: SessionRecordStore,
    preferences: Preferences,
    shell: LobbyShell,
    redis: SharedRedis,
    issuer_calls: IssuerCalls | None = None,
) -> tuple[WebAuth, WebAuth]:
    """The lobby's auth and the talk host's, over one identity-writes owner.

    Each host spends its own budgets in the Redis every pod shares.

    Each host has its own relying party and its own signing key, so neither
    accepts the other's cookies or pending logins; both write the one
    `sessions` table, so they enter the one transaction under its one write
    lock, which also reports to the one audit sink.
    """
    lobby_client, talk_client = client_configs_of(settings)
    admission = admission_of(settings)
    lobby = lobby_web_auth(
        config=web_auth_config_for(
            settings.secret_key,
            origin=settings.public_origin,
            rate_limits=redis.rate_limits(RateLimitedHost.LOBBY),
        ),
        client=lobby_client,
        admission=admission,
        sign_in=sign_in,
        sessions=sessions,
        preferences=preferences,
        shell=shell,
        own_body_readers=frozenset({MCP_ENDPOINT_PATH}),
        issuer_calls=issuer_calls,
    )
    talk = talk_web_auth(
        config=web_auth_config_for(
            SecretStr(talk_session_secret(settings.secret_key.get_secret_value())),
            origin=settings.talk_origin,
            rate_limits=redis.rate_limits(RateLimitedHost.TALK),
        ),
        client=talk_client,
        admission=admission,
        sign_in=sign_in,
        sessions=sessions,
        lobby_origin=settings.public_origin,
        issuer_calls=issuer_calls,
    )
    return lobby, talk


def _preferences(stores: Stores) -> Preferences:
    return Preferences(
        instance=stores.instance_settings, people=stores.person_preferences
    )


def _session_sweep(sessions: PostgresSessions, clock: SystemClock) -> Schedule:
    """Delete expired sessions on a schedule, reading the clock on every run."""
    return Schedule(
        chore=lambda: sessions.delete_expired(clock.now()),
        chore_code=ChoreCode.SESSION_SWEEP,
        interval=SESSION_SWEEP_INTERVAL,
    )


def web_auth_config_for(
    secret: SecretStr,
    *,
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
    issuer_calls: IssuerCalls | None = None,
    migrations_directory: Path = MIGRATIONS_DIRECTORY,
) -> None:
    """Migrate the database or unpin a host when asked to, else serve the lobby.

    Each runs on the configuration the environment carries. A refused command
    line ends the process in one `process.failed` line and exit status 2; a
    refused configuration, or a store fault, in one such line and exit status
    1; a SIGINT or a SIGTERM, at any point, ends it as a stop in one
    `process.stopped` line.
    """
    configure_logging(SERVICE_NAME)
    with signals_end_as_a_stop() as stop_line:
        cap_alembic_logging()
        try:
            asked = read_the_command_line(arguments)
            if asked.command == MIGRATE_COMMAND:
                migrate_the_database()
                return
            if asked.command == UNPIN_HOST_COMMAND:
                unpin_the_host(asked.host)
                return
            _log.info("process.started")
            settings = load_settings()
            try:
                instance = build_instance(settings, issuer_calls, migrations_directory)
            except StoreFault as fault:
                raise store_refusal(fault, settings.postgres.url) from None
        except DatabaseRefusedError as refused:
            driver_error = refused.driver_error
            codes = {} if driver_error is None else {"error_type": driver_error}
            end_failed(refused.reason, **codes)
        except ConfigurationError as refused:
            # The settings by their variables; the message may name a path.
            end_failed(
                ServerReason.CONFIGURATION_REFUSED, setting=",".join(refused.variables)
            )
        asyncio.run(_serve(instance, settings, stop_line))


def served_by_host(instance: Instance, settings: Settings) -> ASGIApp:
    """The app that answers each hostname, and one route for any other.

    The `Host` header alone chooses: a request for a hostname this instance does
    not serve, with a port or without, or with none, gets the router's 404 and a
    socket is refused, so neither app ever answers under the other's name. The
    one exception is the lobby's back-channel logout path: the identity provider
    reaches it under the name of the network it shares with the instance, and
    the signed Logout Token is its only control. Other methods on that path
    answer 405. The talk hostname stays the talk app's alone, so the path is a
    404 there. Nothing the caller sent builds a redirect.

    webauth's front wraps the router, so the stacks behind it and the router's
    own 404 and 405 name the client the trusted proxies resolve.

    While the app runs, it polls the sources and sweeps the sessions; its end
    waits for a chore in flight, then closes the instance.
    """

    @asynccontextmanager
    async def lifespan(_: Router) -> AsyncGenerator[None]:
        # uvicorn awaits the lifespan's end on every stop, a signal's included,
        # so the chores stop and the instance closes here, before the server
        # writes its stop line. Starlette runs only this router's lifespan, never
        # a `Host` sub-app's, so the lobby's own is entered here.
        try:
            async with (
                instance.lobby.router.lifespan_context(instance.lobby),
                instance.poller.polling(),
                instance.session_sweep.polling(),
            ):
                yield
        finally:
            try:
                await instance.aclose()
            finally:
                instance.close()

    return web_auth_front(
        Router(
            routes=[
                Host(str(urlsplit(settings.public_origin).hostname), instance.lobby),
                Host(str(urlsplit(settings.talk_origin).hostname), instance.talk),
                Route(BACKCHANNEL_LOGOUT_PATH, instance.lobby, methods=["POST"]),
            ],
            redirect_slashes=False,
            lifespan=lifespan,
        ),
        trusted_proxies=instance.trusted_proxies,
    )


async def _serve(instance: Instance, settings: Settings, stop_line: StopLine) -> None:
    """Serve the instance until a signal stops it, then write `stop_line`."""
    served = served_config(
        served_by_host(instance, settings), host=settings.host, port=settings.port
    )
    await StoppingServer(served, instance.stopping, stop_line).serve()
