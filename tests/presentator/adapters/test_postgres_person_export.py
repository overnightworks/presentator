"""Exporting a person over real Postgres: no table that reaches an account is missed."""

from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.contracts.people import KeyDraftExport, PersonKind
from tests.presentator.adapters.support.key_draft_support import a_key_drafts_store
from tests.presentator.adapters.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
    WHAT_THE_GIT_HOST_EXPECTS,
)
from tests.presentator.adapters.support.person_postgres_support import (
    PostgresInstance,
    an_instance_with_both_people_on_postgres,
    tables_reaching_accounts,
)
from tests.support.people import a_session_id

_TWO_SESSIONS = 2
_NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)

_KIND_OF_TABLE = {
    "users": PersonKind.ACCOUNT,
    "sessions": PersonKind.SESSIONS,
    "person_preferences": PersonKind.PREFERENCES,
    "source_key_drafts": PersonKind.KEY_DRAFT,
    "sources": PersonKind.SOURCES,
    "decks": PersonKind.DECKS,
    "source_runs": PersonKind.RUNS,
}


@pytest.fixture
def instance(tmp_path: Path, database_url: str) -> Iterator[PostgresInstance]:
    with an_instance_with_both_people_on_postgres(tmp_path, database_url) as each:
        yield each


def test_every_table_reaching_accounts_is_a_kind_the_export_carries(
    instance: PostgresInstance,
) -> None:
    with instance.engine.connect() as connection:
        reaching = tables_reaching_accounts(connection)

    assert reaching == set(_KIND_OF_TABLE)


@pytest.mark.parametrize("column", ["user_id", "owner_id"])
def test_a_table_with_an_account_column_but_no_reference_reaches_accounts(
    instance: PostgresInstance, column: str
) -> None:
    """A later table with only a user_id or owner_id reaches accounts."""
    with instance.engine.begin() as connection:
        connection.exec_driver_sql(
            f"CREATE TABLE added_later (id INTEGER, {column} TEXT)"
        )
        reaching = tables_reaching_accounts(connection)

    assert "added_later" in reaching


def test_the_secret_columns_are_counted_withheld(instance: PostgresInstance) -> None:
    """PE21: each withheld kind has its count."""
    export = instance.people.export_person(THE_PERSON.id)

    assert {entry.kind: entry.count for entry in export.withheld} == {
        PersonKind.SOURCE_CREDENTIAL: 1,
        PersonKind.HOOK_SECRET_HASH: 1,
        PersonKind.KEY_DRAFT_PRIVATE_KEY: 1,
        PersonKind.REFRESH_TOKEN: 1,
        PersonKind.ID_TOKEN: 1,
    }


def _another_session(instance: PostgresInstance, *, id_token: SecretStr | None) -> None:
    PostgresSessionRecordStore(instance.engine).create(
        THE_PERSON.id,
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        refresh_token=None,
        id_token=id_token,
        account_language=None,
    )


def test_two_sessions_holding_id_tokens_are_counted_two(
    instance: PostgresInstance,
) -> None:
    """PE22: two ID-token sessions give `id-token`, `secret`, count 2."""
    _another_session(instance, id_token=SecretStr("a-second-id-token"))

    export = instance.people.export_person(THE_PERSON.id)

    assert {entry.kind: entry.count for entry in export.withheld}[
        PersonKind.ID_TOKEN
    ] == _TWO_SESSIONS
    assert "a-second-id-token" not in str(asdict(export))


def test_sessions_holding_no_id_token_leave_no_id_token_entry(
    instance: PostgresInstance,
) -> None:
    """PE22: the talk host's NULL-token sessions get no `id-token` entry."""
    with instance.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE sessions SET id_token = NULL")
    _another_session(instance, id_token=None)

    export = instance.people.export_person(THE_PERSON.id)

    assert export.data.sessions
    assert PersonKind.ID_TOKEN not in {entry.kind for entry in export.withheld}


def test_a_draft_that_no_longer_opens_is_shown_without_its_key_and_counted(
    instance: PostgresInstance,
) -> None:
    """PE21: an unreadable draft stays in the data, no key, one private key."""
    a_key_drafts_store(instance.engine).remove_for_owner(THE_PERSON.id)
    a_key_drafts_store(
        instance.engine, instance_key="a different instance key of 32ch"
    ).mint(THE_PERSON.id, at=_NOW)

    export = instance.people.export_person(THE_PERSON.id)

    assert export.data.key_draft == KeyDraftExport(public_key=None, created_at=None)
    assert {entry.kind: entry.count for entry in export.withheld}[
        PersonKind.KEY_DRAFT_PRIVATE_KEY
    ] == 1


def test_the_export_holds_no_secret(instance: PostgresInstance) -> None:
    shown = str(asdict(instance.people.export_person(THE_PERSON.id)))

    seeded_secrets = (
        WHAT_THE_GIT_HOST_EXPECTS,
        repr(b"\x11" * 32),
        "PRIVATE KEY",
        "a-refresh-token",
        "an-id-token",
    )
    assert not [secret for secret in seeded_secrets if secret in shown]


def test_an_unknown_subject_exports_nothing_and_creates_no_account(
    instance: PostgresInstance,
) -> None:
    export = instance.people.export_person("nobody-at-all")

    assert export.withheld == ()
    assert export.data.account is None
    assert export.data.sources == ()
    with instance.engine.connect() as connection:
        accounts = connection.exec_driver_sql("SELECT id FROM users").all()
    assert sorted(accounts) == [(ANOTHER.id,), (THE_PERSON.id,)]
