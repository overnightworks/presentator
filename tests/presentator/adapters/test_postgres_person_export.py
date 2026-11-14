"""Exporting a person over real Postgres: no table that reaches an account is missed."""

from collections.abc import Iterator
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from presentator.contracts.people import PersonExport, PersonKind
from tests.presentator.adapters.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
    WHAT_THE_GIT_HOST_EXPECTS,
    an_instance_with_both_people,
)
from tests.presentator.adapters.support.person_postgres_support import (
    PostgresInstance,
    an_instance_with_both_people_on_postgres,
    tables_reaching_accounts,
)

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


def _without_the_random_key(export: PersonExport) -> PersonExport:
    """The export with the generated public key of the draft replaced by a marker."""
    draft = export.data.key_draft
    assert draft is not None
    assert draft.public_key
    return replace(
        export,
        data=replace(export.data, key_draft=replace(draft, public_key="a key")),
    )


def test_every_table_reaching_accounts_is_a_kind_the_export_carries(
    instance: PostgresInstance,
) -> None:
    with instance.engine.connect() as connection:
        reaching = tables_reaching_accounts(connection)

    assert reaching == set(_KIND_OF_TABLE)


def test_the_export_is_that_over_sqlite_with_the_same_kinds_withheld(
    instance: PostgresInstance, tmp_path: Path
) -> None:
    (tmp_path / "sqlite").mkdir()
    over_sqlite = an_instance_with_both_people(tmp_path / "sqlite")

    through_sqlite = over_sqlite.people.export_person(THE_PERSON.id)
    through_postgres = instance.people.export_person(THE_PERSON.id)

    assert _without_the_random_key(through_postgres) == _without_the_random_key(
        through_sqlite
    )
    assert {entry.kind for entry in through_postgres.withheld} == {
        PersonKind.SOURCE_CREDENTIAL,
        PersonKind.HOOK_SECRET_HASH,
        PersonKind.KEY_DRAFT_PRIVATE_KEY,
        PersonKind.REFRESH_TOKEN,
        PersonKind.ID_TOKEN,
    }


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
