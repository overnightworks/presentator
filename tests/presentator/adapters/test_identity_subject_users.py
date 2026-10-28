"""The accounts an identity provider vouches for, in the users table."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from webauth.ports import (
    AccountEvent,
    AccountEventKind,
    SignInStores,
    UsernameTakenError,
)

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSubjectUserStore,
    create_identity_tables,
)
from presentator.contracts.models import Role


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database = tmp_path / "identity.sqlite3"
    create_identity_tables(database)
    return database


@pytest.fixture
def accounts(database: Path) -> SqliteSubjectUserStore:
    return SqliteSubjectUserStore(database)


def test_an_account_created_for_a_subject_is_found_by_that_subject(
    accounts: SqliteSubjectUserStore,
) -> None:
    created = accounts.create_for_subject("subject-1", "felix", "admin")

    found = accounts.get("subject-1")

    assert found is not None
    assert (found.id, found.username, found.role) == ("subject-1", "felix", "admin")
    assert created.id == found.id


def test_a_subject_that_differs_only_in_case_is_another_account(
    accounts: SqliteSubjectUserStore,
) -> None:
    accounts.create_for_subject("Subject-1", "felix", "user")

    assert accounts.get("subject-1") is None
    assert accounts.get("Subject-1 ") is None


def test_a_username_another_account_holds_is_refused_as_taken(
    accounts: SqliteSubjectUserStore,
) -> None:
    accounts.create_for_subject("subject-1", "felix", "user")

    with pytest.raises(UsernameTakenError):
        accounts.create_for_subject("subject-2", "felix", "user")

    assert accounts.get("subject-2") is None


@pytest.mark.parametrize("second_username", ["anna", "felix"])
def test_a_subject_that_already_has_an_account_is_not_a_taken_username(
    accounts: SqliteSubjectUserStore,
    second_username: str,
) -> None:
    accounts.create_for_subject("subject-1", "felix", "user")

    with pytest.raises(sqlite3.IntegrityError) as refused:
        accounts.create_for_subject("subject-1", second_username, "user")

    assert not isinstance(refused.value, UsernameTakenError)


def test_a_changed_role_is_stored_and_returned(
    accounts: SqliteSubjectUserStore,
) -> None:
    accounts.create_for_subject("subject-1", "felix", "user")

    changed = accounts.set_role("subject-1", Role.ADMIN.value)

    assert changed.role == Role.ADMIN
    stored = accounts.get("subject-1")
    assert stored is not None
    assert stored.role == Role.ADMIN


def test_a_role_for_an_account_that_does_not_exist_raises_and_writes_nothing(
    accounts: SqliteSubjectUserStore,
    database: Path,
) -> None:
    with pytest.raises(LookupError):
        accounts.set_role("nobody", Role.ADMIN.value)

    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT count(*) FROM users").fetchone() == (0,)


def _fail_after(
    writes: SqliteIdentityWrites, write: Callable[[SignInStores], object]
) -> None:
    """Enter the transaction, write, and let the block raise."""
    with writes() as stores:
        write(stores)
        message = "the block fails after the write"
        raise RuntimeError(message)


def test_a_role_changed_in_an_entry_that_raises_is_rolled_back(
    accounts: SqliteSubjectUserStore, database: Path
) -> None:
    accounts.create_for_subject("subject-1", "felix", "user")
    writes = SqliteIdentityWrites(database, ProcessWriteLock(), LoggingAuditSink())

    with pytest.raises(RuntimeError):
        _fail_after(
            writes, lambda stores: stores.accounts.set_role("subject-1", "admin")
        )

    stored = accounts.get("subject-1")
    assert stored is not None
    assert stored.role == Role.USER


def test_a_role_changed_in_an_entry_reads_back_only_after_the_entry_commits(
    accounts: SqliteSubjectUserStore, database: Path
) -> None:
    accounts.create_for_subject("subject-1", "felix", "user")
    writes = SqliteIdentityWrites(database, ProcessWriteLock(), LoggingAuditSink())

    with writes() as stores:
        stores.accounts.set_role("subject-1", Role.ADMIN.value)
        during = accounts.get("subject-1")
    after = accounts.get("subject-1")

    assert during is not None
    assert during.role == Role.USER
    assert after is not None
    assert after.role == Role.ADMIN


@dataclass
class _Sink:
    events: list[AccountEvent] = field(default_factory=list[AccountEvent])

    def account_event(self, event: AccountEvent) -> None:
        self.events.append(event)


_AN_EVENT = AccountEvent(
    kind=AccountEventKind.BACKCHANNEL_LOGOUT, actor_id=None, subject_id="subject-1"
)


def test_the_audit_events_of_an_entry_reach_the_sink_only_after_it_commits(
    database: Path,
) -> None:
    sink = _Sink()
    writes = SqliteIdentityWrites(database, ProcessWriteLock(), sink)

    with writes() as stores:
        stores.audit.account_event(_AN_EVENT)
        during = list(sink.events)

    assert during == []
    assert sink.events == [_AN_EVENT]


def test_the_audit_events_of_an_entry_that_raises_are_dropped(database: Path) -> None:
    sink = _Sink()
    writes = SqliteIdentityWrites(database, ProcessWriteLock(), sink)

    with pytest.raises(RuntimeError):
        _fail_after(writes, lambda stores: stores.audit.account_event(_AN_EVENT))

    assert sink.events == []
