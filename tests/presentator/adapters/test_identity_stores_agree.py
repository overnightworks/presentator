"""The SQLite and the Postgres identity stores answer the same to the same writes."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import SecretStr
from webauth.ports import AccountKind, UsernameTakenError

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SqliteSubjectUserStore,
    create_identity_tables,
)
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role
from presentator.contracts.people import SessionExport
from tests.support.postgres import engine_on

_TWO_HOURS_EAST = timezone(timedelta(hours=2))
_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)


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


def _postgres_stores(database_url: str) -> Iterator[_Stores]:
    engine = engine_on(database_url)
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
