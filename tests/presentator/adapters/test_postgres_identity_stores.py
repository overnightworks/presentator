"""The Postgres identity stores: taken names, deleted accounts, and the sign-in lock."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from webauth.ports import (
    AccountEvent,
    AccountEventKind,
    AccountKind,
    AuditSink,
    UsernameTakenError,
)

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.locks import SIGN_IN_LOCK, parameters
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role
from tests.support.postgres import engine_on

_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
_AN_EVENT = AccountEvent(
    kind=AccountEventKind.BACKCHANNEL_LOGOUT, actor_id=None, subject_id="subject-1"
)
_TRY_SIGN_IN_LOCK = text("SELECT pg_try_advisory_xact_lock(:lock_class, :instance)")


class _RefusedError(Exception):
    pass


def _postgres_writes(stores: _Stores, sink: AuditSink) -> PostgresIdentityWrites:
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
    """The stores over a database of this test's own."""

    accounts: PostgresSubjectUserStore
    sessions: PostgresSessionRecordStore
    writes: PostgresIdentityWrites


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
def stores(database_url: str) -> Iterator[_Stores]:
    yield from _postgres_stores(database_url)


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


def test_an_entry_still_holds_the_sign_in_lock_while_its_events_reach_the_sink(
    stores: _Stores, database_url: str
) -> None:
    other_engine = engine_on(database_url)
    sink = _LockProbingSink(other_engine)
    writes = _postgres_writes(stores, sink)
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


def test_an_entry_that_raises_rolls_back_drops_its_events_and_frees_the_lock(
    stores: _Stores, database_url: str
) -> None:
    sink = _RecordingSink()
    writes = _postgres_writes(stores, sink)

    with pytest.raises(_RefusedError):
        _enter_and_raise(writes)

    assert writes.get("rolled-back") is None
    assert stores.sessions.load("rolled-back-session") is None
    assert sink.events == []
    other_engine = engine_on(database_url)
    try:
        assert _sign_in_lock_is_free(other_engine)
    finally:
        other_engine.dispose()
