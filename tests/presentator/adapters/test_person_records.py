"""The per-person deletes and reads of the stores, over real Postgres."""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.preferences import PostgresPersonPreferencesStore
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role, User
from presentator.contracts.people import SessionExport
from presentator.contracts.preferences import PersonPreferences, ThemeChoice
from tests.presentator.adapters.support.key_draft_support import a_key_drafts_store
from tests.support.people import a_session_id, seed_user

_NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)
_THE_PERSON = User(id="the-person", username="pia", role=Role.USER)
_ANOTHER = User(id="another-person", username="ben", role=Role.USER)
_A_REFRESH_WORD = "a-word-nobody-may-read"


@pytest.fixture
def database(engine: Engine) -> Engine:
    for person in (_THE_PERSON, _ANOTHER):
        seed_user(engine, person)
    return engine


def test_deleting_an_account_takes_its_sessions_and_leaves_another_account(
    database: Engine,
) -> None:
    """PE1: the account goes with its sessions; no other account is touched."""
    sessions = PostgresSessionRecordStore(database)
    accounts = PostgresIdentityWrites(database, LoggingAuditSink())
    for person in (_THE_PERSON, _ANOTHER):
        sessions.create(
            person.id,
            session_id=a_session_id(),
            now=_NOW,
            expires_at=_NOW + timedelta(hours=1),
            refresh_token=None,
            id_token=SecretStr("an-id-token"),
            account_language=None,
        )

    accounts.delete_account(_THE_PERSON.id)

    assert accounts.get(_THE_PERSON.id) is None
    assert sessions.sessions_of(_THE_PERSON.id) == ()
    assert accounts.get(_ANOTHER.id) == _ANOTHER
    assert len(sessions.sessions_of(_ANOTHER.id)) == 1


def test_asking_for_an_unknown_account_creates_none(database: Engine) -> None:
    """PE3, PE10: an unknown subject creates no `users` row."""
    accounts = PostgresIdentityWrites(database, LoggingAuditSink())

    assert accounts.get("nobody-at-all") is None
    with database.connect() as connection:
        found = connection.execute(
            text("SELECT id FROM users WHERE id = :id"), {"id": "nobody-at-all"}
        )
        assert found.all() == []


def test_the_sessions_of_a_person_carry_their_times_and_no_secret(
    database: Engine,
) -> None:
    """PE21: sessions are counted by their times and never show the token."""
    sessions = PostgresSessionRecordStore(database)
    sessions.create(
        _THE_PERSON.id,
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        refresh_token=SecretStr(_A_REFRESH_WORD),
        id_token=SecretStr("an-id-token"),
        account_language=None,
    )

    found = sessions.sessions_of(_THE_PERSON.id)

    assert found == (
        SessionExport(
            created_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
            account_language=None,
        ),
    )
    assert _A_REFRESH_WORD not in repr(found)


@pytest.mark.parametrize("language", ["de", None])
def test_a_persons_session_carries_the_account_language_stored_at_its_sign_in(
    database: Engine, language: str | None
) -> None:
    """PE31: the language is exported as stored, `null` where the account named none."""
    sessions = PostgresSessionRecordStore(database)
    sessions.create(
        _THE_PERSON.id,
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        refresh_token=None,
        id_token=None,
        account_language=language,
    )

    (found,) = sessions.sessions_of(_THE_PERSON.id)

    assert found.account_language == language


def test_only_sessions_holding_a_refresh_token_are_counted_as_holding_one(
    database: Engine,
) -> None:
    """PE21: a session without a refresh token is not counted as holding one."""
    sessions = PostgresSessionRecordStore(database)
    for token in (SecretStr(_A_REFRESH_WORD), None):
        sessions.create(
            _THE_PERSON.id,
            session_id=a_session_id(),
            now=_NOW,
            expires_at=_NOW + timedelta(hours=1),
            refresh_token=token,
            id_token=SecretStr("an-id-token"),
            account_language=None,
        )

    assert sessions.secrets_held(_THE_PERSON.id).refresh_tokens == 1
    assert sessions.secrets_held(_ANOTHER.id).refresh_tokens == 0


def test_deleting_preferences_removes_only_that_persons_row(database: Engine) -> None:
    """PE1: preferences go per person and a repeat deletes zero."""
    preferences = PostgresPersonPreferencesStore(database)
    for person in (_THE_PERSON, _ANOTHER):
        preferences.write(person.id, PersonPreferences(theme=ThemeChoice.DARK))

    assert preferences.delete(_THE_PERSON.id) == 1
    assert preferences.delete(_THE_PERSON.id) == 0

    assert preferences.read(_THE_PERSON.id) is None
    assert preferences.read(_ANOTHER.id) == PersonPreferences(theme=ThemeChoice.DARK)


def test_a_draft_is_counted_and_removed_whether_or_not_its_key_still_opens(
    database: Engine,
) -> None:
    """PE1: key drafts are counted and removed though their key is gone."""
    minted = a_key_drafts_store(database, instance_key="the key another instance had")
    minted.mint(_THE_PERSON.id, at=_NOW)
    minted.mint(_ANOTHER.id, at=_NOW)
    drafts = a_key_drafts_store(database)
    assert drafts.unconsumed_for(_THE_PERSON.id, newer_than=_NOW) is None

    assert drafts.count_for_owner(_THE_PERSON.id) == 1
    assert drafts.remove_for_owner(_THE_PERSON.id) == 1

    assert drafts.count_for_owner(_THE_PERSON.id) == 0
    assert drafts.count_for_owner(_ANOTHER.id) == 1
