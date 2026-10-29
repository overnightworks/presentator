"""Shared arrangement for the sign-in tests: a lobby, a fake issuer, real stores."""

import asyncio
import sqlite3
import threading
from collections.abc import Callable, Generator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from fastapi.testclient import TestClient
from httpx2 import Response as BrowserAnswer
from webauth.config import WebAuthConfig
from webauth.oidc import OidcRelyingParty, TokenRequestPost
from webauth.oidc_login import AdmissionConfig
from webauth.ports import AccountEvent, AuditSink, SignInStores, SignInTransaction

from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SqliteSubjectUserStore,
    create_identity_tables,
)
from tests.presentator.application.support.fakes import FrozenClock
from tests.presentator.application.support.person_instance_support import an_instance
from tests.support.fake_issuer import FakeIssuer
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_CLIENT_ID,
    AN_ISSUER,
    Lobby,
    NoSignInTransaction,
    SigningIn,
    a_browser_at,
    a_crowd,
    a_lobby_app,
    a_relying_party,
    an_admin_people,
    an_installed_auth,
)

MEMBER = ["/apps/presentator"]
ADMIN = ["/apps/presentator", "/apps/presentator/admin"]
_COUNTS = {
    "users": "SELECT count(*) FROM users",
    "sessions": "SELECT count(*) FROM sessions",
}


def on_an_event_loop() -> bool:
    """Whether the caller runs on an asyncio event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@dataclass
class RecordingAudit:
    """The audit lines a sign-in wrote."""

    events: list[AccountEvent] = field(default_factory=list[AccountEvent])

    def account_event(self, event: AccountEvent) -> None:
        self.events.append(event)


@dataclass(frozen=True, slots=True, kw_only=True)
class Scene:
    """A lobby that signs people in through a fake issuer, over real stores."""

    lobby: Lobby
    issuer: FakeIssuer
    database: Path
    users: SqliteSubjectUserStore
    audit: RecordingAudit
    sessions: SqliteSessionRecordStore
    relying_party: OidcRelyingParty
    config: WebAuthConfig
    clock: FrozenClock

    def browser(self, *, raise_server_exceptions: bool = True) -> TestClient:
        return TestClient(
            self.lobby.app,
            base_url=A_BROWSERS_ADDRESS,
            follow_redirects=False,
            headers=A_BROWSERS_HEADERS,
            raise_server_exceptions=raise_server_exceptions,
        )

    def begin(self, browser: TestClient, next_path: str | None = None) -> str:
        """Start the sign-in and return the issuer's authorization address."""
        params = {} if next_path is None else {"next": next_path}
        return browser.get("/login/oidc", params=params).headers["location"]

    def sign_in(
        self,
        browser: TestClient | None = None,
        *,
        subject: str = "subject-1",
        groups: list[str] | None = None,
        username: str | None = "felix",
        next_path: str | None = None,
    ) -> BrowserAnswer:
        """A person signs in at the issuer and the browser comes back."""
        client = browser or self.lobby.client
        query = self.issuer.authorize(
            self.begin(client, next_path),
            subject=subject,
            groups=MEMBER if groups is None else groups,
            username=username,
        )
        return client.get(f"/auth/callback?{query}")

    def stored_sessions(self) -> dict[str, str | None]:
        """The refresh token of every session row by its id, as the file holds it."""
        with closing(sqlite3.connect(self.database)) as connection:
            return dict(connection.execute("SELECT id, refresh_token FROM sessions"))

    def stored_id_tokens(self) -> list[str | None]:
        """The ID token of every session row, as the file holds it."""
        with closing(sqlite3.connect(self.database)) as connection:
            return [
                token
                for (token,) in connection.execute("SELECT id_token FROM sessions")
            ]

    def stored_tokens(self) -> list[str | None]:
        return list(self.stored_sessions().values())

    def count(self, table: Literal["users", "sessions"]) -> int:
        with closing(sqlite3.connect(self.database)) as connection:
            (count,) = connection.execute(_COUNTS[table]).fetchone()
        return int(count)


def a_scene(  # noqa: PLR0913
    tmp_path: Path,
    *,
    transaction: Callable[[SqliteIdentityWrites], SignInTransaction] = lambda real: (
        real
    ),
    lock: ProcessWriteLock | None = None,
    audit: Callable[[RecordingAudit], AuditSink] = lambda recording: recording,
    admission: AdmissionConfig | None = None,
    token_requests: Callable[[TokenRequestPost], TokenRequestPost] = lambda real: real,
    token_clients: frozenset[str] = frozenset(),
) -> Scene:
    database = tmp_path / "identity.sqlite3"
    create_identity_tables(database)
    people = a_crowd()
    issuer = FakeIssuer(issuer=AN_ISSUER, client_id=A_CLIENT_ID, now=people.clock.now())
    users = SqliteSubjectUserStore(database)
    recording = RecordingAudit()
    writes = SqliteIdentityWrites(
        database, ProcessWriteLock() if lock is None else lock, audit(recording)
    )
    auth = an_installed_auth(
        people.clock,
        relying_party=a_relying_party(
            people.clock,
            post_token_request=token_requests(issuer.post_token_request),
            fetch_key_set=issuer.fetch_key_set,
        ),
        transaction=transaction(writes),
    )
    sessions = SqliteSessionRecordStore(database)
    app, _ = a_lobby_app(
        people=people,
        signing_in=SigningIn(
            sessions=sessions,
            auth=auth if admission is None else replace(auth, admission=admission),
        ),
        admin_people=an_admin_people(an_instance().people, clients=token_clients)
        if token_clients
        else None,
    )
    lobby = a_browser_at(
        app, people=people, base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    )
    return Scene(
        lobby=lobby,
        issuer=issuer,
        database=database,
        users=users,
        audit=recording,
        sessions=sessions,
        relying_party=auth.relying_party,
        config=auth.config,
        clock=people.clock,
    )


def cookie_names(answer: BrowserAnswer) -> list[str]:
    return [cookie.split("=", 1)[0] for cookie in answer.headers.get_list("set-cookie")]


@dataclass
class ContentionSignalingLock:
    """A real re-entrant lock that says when a taker found it held."""

    contended: threading.Event = field(default_factory=threading.Event)
    _real: threading.RLock = field(default_factory=threading.RLock)

    def __enter__(self) -> bool:
        """Take the lock, announcing first when it was already held."""
        if self._real.acquire(blocking=False):
            return True
        self.contended.set()
        return self._real.acquire()

    def __exit__(self, *_exception: object) -> None:
        """Let go of the lock."""
        self._real.release()


@dataclass
class TransactionDouble:
    """The real transaction, watched: when it is entered and left, what it hands out.

    `around` is what `a_scene` takes, so the test keeps the double it reads.
    """

    real: SignInTransaction = field(default_factory=NoSignInTransaction)
    log: list[tuple[str, object]] = field(default_factory=list[tuple[str, object]])
    inside: Callable[[], None] = lambda: None
    through: Callable[[SignInStores], SignInStores] = lambda stores: stores
    sessions_seen: Callable[[], int] = lambda: 0

    def around(self, real: SignInTransaction) -> TransactionDouble:
        self.real = real
        return self

    @contextmanager
    def __call__(self) -> Generator[SignInStores]:
        self.log.append(("entered", on_an_event_loop()))
        with self.real() as stores:
            self.inside()
            yield self.through(stores)
        self.log.append(("left", self.sessions_seen()))
