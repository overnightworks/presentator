"""The SQLite and the Postgres identity stores answer the same to the same writes."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from webauth.ports import (
    AccountEvent,
    AccountEventKind,
    AccountKind,
    AuditSink,
    UsernameTakenError,
)

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SqliteSubjectUserStore,
    create_identity_tables,
)
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.locks import SIGN_IN_LOCK, parameters
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role
from presentator.contracts.people import SessionExport
from tests.support.postgres import engine_on

_TWO_HOURS_EAST = timezone(timedelta(hours=2))
_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
# Not UTC, so a read that skips the conversion to UTC renders a different offset.
_SERVER_TIME_ZONE = "Asia/Tokyo"
_AN_EVENT = AccountEvent(
    kind=AccountEventKind.BACKCHANNEL_LOGOUT, actor_id=None, subject_id="subject-1"
)
_TRY_SIGN_IN_LOCK = text("SELECT pg_try_advisory_xact_lock(:lock_class, :instance)")


class _RefusedError(Exception):
    pass


def _postgres_writes(stores: _Stores, sink: AuditSink) -> PostgresIdentityWrites:
    assert isinstance(stores.writes, PostgresIdentityWrites)
    return PostgresIdentityWrites(stores.writes.engine, sink)


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[AccountEvent] = []

    def account_event(self, event: AccountEvent) -> None:
        self.events.append(event)


class _LockProbingSink(_RecordingSink):
    """Records, for each event, whether another connection could take the lock."""

    def __init__(self, other_engine: Engine) -> None:
        super().__init__()
        self.other_engine = other_engine
        self.lock_free_when_delivered: list[bool] = []

    def account_event(self, event: AccountEvent) -> None:
        super().account_event(event)
        self.lock_free_when_delivered.append(_sign_in_lock_is_free(self.other_engine))


def _sign_in_lock_is_free(other_engine: Engine) -> bool:
    with other_engine.begin() as other_connection:
        return bool(
            other_connection.execute(
                _TRY_SIGN_IN_LOCK, parameters(SIGN_IN_LOCK)
            ).scalar_one()
        )


@dataclass(frozen=True, slots=True)
class _Stores:
    """One adapter's stores over a database of its own."""

    accounts: SqliteSubjectUserStore | PostgresSubjectUserStore
    sessions: SqliteSessionRecordStore | PostgresSessionRecordStore
    writes: SqliteIdentityWrites | PostgresIdentityWrites


def _sqlite_stores(directory: Path) -> _Stores:
    database = directory / "identity.sqlite3"
    create_identity_tables(database)
    return _Stores(
        accounts=SqliteSubjectUserStore(database),
        sessions=SqliteSessionRecordStore(database),
        writes=SqliteIdentityWrites(database, ProcessWriteLock(), LoggingAuditSink()),
    )


def _in_server_time_zone(database_url: str) -> str:
    url = make_url(database_url).update_query_dict(
        {"options": f"-c TimeZone={_SERVER_TIME_ZONE}"}
    )
    return url.render_as_string(hide_password=False)


def _postgres_stores(database_url: str) -> Iterator[_Stores]:
    engine = engine_on(_in_server_time_zone(database_url))
    try:
        yield _Stores(
            accounts=PostgresSubjectUserStore(engine),
            sessions=PostgresSessionRecordStore(engine),
            writes=PostgresIdentityWrites(engine, LoggingAuditSink()),
        )
    finally:
        engine.dispose()


@pytest.fixture
def sqlite_stores(tmp_path: Path) -> _Stores:
    return _sqlite_stores(tmp_path)


@pytest.fixture
def postgres_stores(database_url: str) -> Iterator[_Stores]:
    yield from _postgres_stores(database_url)


@pytest.fixture(params=["sqlite", "postgres"])
def stores(request: pytest.FixtureRequest, sqlite_stores: _Stores) -> _Stores:
    if request.param == "sqlite":
        return sqlite_stores
    return request.getfixturevalue("postgres_stores")


def _write_and_read_a_household(stores: _Stores) -> list[object]:
    """Write accounts and sessions that stress every column, reading after each step."""
    accounts, sessions = stores.accounts, stores.sessions
    observed: list[object] = []
    for subject, username, role, kind in (
        ("Sub-é", "Änne", Role.ADMIN, AccountKind.PERSON),
        ("sub-b", "bot", Role.USER, AccountKind.AGENT),
        ("sub-c", "cleo", Role.USER, AccountKind.PERSON),
    ):
        observed.append(
            accounts.create_for_subject(subject, username, role.value, kind)
        )
    observed.append(accounts.set_role("sub-c", Role.ADMIN.value))
    observed.append(accounts.set_kind("sub-c", AccountKind.AGENT))
    observed.extend(
        accounts.get(asked) for asked in ("Sub-é", "sub-é", "Sub-é ", "sub-b", "none")
    )

    for session_id, user_id, created_at, lifetime, tokens, language in (
        ("s1", "Sub-é", _NOON, timedelta(hours=1), ("refresh", "identity"), "de"),
        (
            "s2",
            "Sub-é",
            _NOON.astimezone(_TWO_HOURS_EAST) + timedelta(minutes=5),
            timedelta(hours=1),
            (None, None),
            None,
        ),
        ("s3", "Sub-é", _NOON + timedelta(minutes=10), timedelta(0), ("r", None), "fr"),
        ("s4", "sub-b", _NOON, timedelta(seconds=-1), (None, "identity"), "en"),
    ):
        refresh, identity = tokens
        observed.append(
            sessions.create(
                user_id,
                session_id=session_id,
                now=created_at,
                expires_at=created_at + lifetime,
                refresh_token=None if refresh is None else SecretStr(refresh),
                id_token=None if identity is None else SecretStr(identity),
                account_language=language,
            )
        )
    observed.append(sessions.touch("s2", expires_at=_NOON + timedelta(days=1)))
    observed.append(sessions.touch("gone", expires_at=_NOON))
    observed.append(
        sessions.replace_refresh_token("s2", refresh_token=SecretStr("rotated"))
    )
    observed.extend(sessions.load(each) for each in ("s1", "s2", "s3", "s4", "gone"))
    observed.append(_listed(stores, "Sub-é"))
    observed.append(sessions.secrets_held("Sub-é"))

    # The cutoff floors to the whole second, so `s3` stands at its own second.
    observed.append(
        sessions.delete_expired(_NOON + timedelta(minutes=10, microseconds=500000))
    )
    observed.append(sessions.delete_expired(_NOON + timedelta(minutes=10, seconds=1)))
    sessions.prune_overflow("Sub-é", 1)
    observed.append(_listed(stores, "Sub-é"))
    observed.append(sessions.delete_for_user("Sub-é"))
    return observed


def _listed(stores: _Stores, user_id: str) -> list[SessionExport]:
    return sorted(
        stores.sessions.sessions_of(user_id), key=lambda each: each.created_at
    )


def test_the_same_writes_read_the_same_through_both_adapters(
    sqlite_stores: _Stores, postgres_stores: _Stores
) -> None:
    through_sqlite = _write_and_read_a_household(sqlite_stores)
    through_postgres = _write_and_read_a_household(postgres_stores)

    assert through_postgres == through_sqlite
    # Aware datetimes compare by instant; their rendering shows the offset read.
    assert repr(through_postgres) == repr(through_sqlite)


def test_a_taken_username_is_refused_and_the_entry_stays_usable(
    stores: _Stores,
) -> None:
    stores.accounts.create_for_subject(
        "holder", "taken-name", Role.USER.value, AccountKind.PERSON
    )

    with stores.writes() as entry:
        with pytest.raises(UsernameTakenError):
            entry.accounts.create_for_subject(
                "latecomer", "taken-name", Role.USER.value, AccountKind.PERSON
            )
        entry.accounts.create_for_subject(
            "latecomer", "free-name", Role.USER.value, AccountKind.PERSON
        )

    assert stores.accounts.get("latecomer") is not None
    assert stores.writes.get("holder") is not None


def test_a_subject_that_exists_is_not_reported_as_a_taken_username(
    stores: _Stores,
) -> None:
    stores.accounts.create_for_subject(
        "holder", "name", Role.USER.value, AccountKind.PERSON
    )

    with pytest.raises(Exception) as refused:  # noqa: PT011 - the store's own error type is the adapter's
        stores.accounts.create_for_subject(
            "holder", "name", Role.USER.value, AccountKind.PERSON
        )

    assert not isinstance(refused.value, UsernameTakenError)


def test_deleting_an_account_ends_its_sessions_and_leaves_the_others(
    stores: _Stores,
) -> None:
    for subject in ("leaver", "stayer"):
        stores.accounts.create_for_subject(
            subject, subject, Role.USER.value, AccountKind.PERSON
        )
        stores.sessions.create(
            subject,
            session_id=f"{subject}-session",
            now=_NOON,
            expires_at=_NOON + timedelta(hours=1),
            refresh_token=None,
            id_token=None,
            account_language=None,
        )

    stores.writes.delete_account("leaver")

    assert stores.writes.get("leaver") is None
    assert stores.sessions.load("leaver-session") is None
    assert stores.writes.get("stayer") is not None
    assert stores.sessions.load("stayer-session") is not None


def test_a_postgres_entry_still_holds_the_sign_in_lock_while_its_events_reach_the_sink(
    postgres_stores: _Stores, database_url: str
) -> None:
    other_engine = engine_on(database_url)
    sink = _LockProbingSink(other_engine)
    writes = _postgres_writes(postgres_stores, sink)
    try:
        with writes() as entry:
            entry.audit.account_event(_AN_EVENT)
    finally:
        other_engine.dispose()

    assert sink.events == [_AN_EVENT]
    assert sink.lock_free_when_delivered == [False]


def _enter_and_raise(writes: PostgresIdentityWrites) -> None:
    with writes() as entry:
        entry.accounts.create_for_subject(
            "rolled-back", "rolled-back", Role.USER.value, AccountKind.PERSON
        )
        entry.sessions.create(
            "rolled-back",
            session_id="rolled-back-session",
            now=_NOON,
            expires_at=_NOON + timedelta(hours=1),
            refresh_token=None,
            id_token=None,
            account_language=None,
        )
        entry.audit.account_event(_AN_EVENT)
        raise _RefusedError


def test_a_postgres_entry_that_raises_rolls_back_drops_its_events_and_frees_the_lock(
    postgres_stores: _Stores, database_url: str
) -> None:
    sink = _RecordingSink()
    writes = _postgres_writes(postgres_stores, sink)

    with pytest.raises(_RefusedError):
        _enter_and_raise(writes)

    assert writes.get("rolled-back") is None
    assert postgres_stores.sessions.load("rolled-back-session") is None
    assert sink.events == []
    other_engine = engine_on(database_url)
    try:
        assert _sign_in_lock_is_free(other_engine)
    finally:
        other_engine.dispose()
