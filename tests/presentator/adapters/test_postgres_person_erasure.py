"""Erasing a person over real Postgres and real mirror and build directories."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from presentator.contracts.people import PersonErased
from tests.presentator.adapters.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
)
from tests.presentator.adapters.support.person_postgres_support import (
    PostgresInstance,
    an_instance_with_both_people_on_postgres,
    owned_row_keys,
    rows_still_there,
)


@pytest.fixture
def instance(tmp_path: Path, database_url: str) -> Iterator[PostgresInstance]:
    with an_instance_with_both_people_on_postgres(tmp_path, database_url) as each:
        yield each


def _owned_by(instance: PostgresInstance, person_id: str) -> dict[str, set[str]]:
    with instance.engine.connect() as connection:
        return owned_row_keys(connection, person_id)


def test_a_person_with_every_kind_of_row_leaves_none_and_no_mirror_or_build(
    instance: PostgresInstance,
) -> None:
    owned = _owned_by(instance, THE_PERSON.id)
    assert all(owned.values())
    assert instance.stored(THE_PERSON)

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(outcome, PersonErased)
    with instance.engine.connect() as connection:
        assert not any(rows_still_there(connection, owned).values())
    assert not any(_owned_by(instance, THE_PERSON.id).values())
    assert not instance.mirror_of(THE_PERSON).exists()
    assert not instance.build_of(THE_PERSON).exists()
    assert not instance.stored(THE_PERSON)


def test_a_second_person_keeps_every_row_mirror_and_build(
    instance: PostgresInstance,
) -> None:
    before = _owned_by(instance, ANOTHER.id)

    instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert _owned_by(instance, ANOTHER.id) == before
    assert instance.mirror_of(ANOTHER).exists()
    assert instance.build_of(ANOTHER).exists()
    assert instance.stored(ANOTHER)


def test_a_dry_run_changes_no_row_and_no_directory(
    instance: PostgresInstance,
) -> None:
    before = _owned_by(instance, THE_PERSON.id)

    instance.people.erase_person(THE_PERSON.id, dry_run=True)

    assert _owned_by(instance, THE_PERSON.id) == before
    assert instance.mirror_of(THE_PERSON).exists()
    assert instance.build_of(THE_PERSON).exists()
    assert instance.stored(THE_PERSON)


def test_an_unknown_subject_creates_no_account(instance: PostgresInstance) -> None:
    instance.people.erase_person("nobody-at-all", dry_run=False)
    instance.people.erase_person("nobody-at-all", dry_run=True)

    with instance.engine.connect() as connection:
        accounts = connection.execute(text("SELECT id FROM users ORDER BY id")).all()
    assert accounts == [(ANOTHER.id,), (THE_PERSON.id,)]


def test_the_sole_admin_is_erased_like_anyone_and_a_repeat_is_at_zero(
    instance: PostgresInstance,
) -> None:
    """PE1, PE3: the sole admin goes like anyone; a repeat is idempotent."""
    first = instance.people.erase_person(THE_PERSON.id, dry_run=False)
    again = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(first, PersonErased)
    assert isinstance(again, PersonErased)
    assert all(entry.count == 0 for entry in again.kinds)


def test_a_build_whose_run_is_no_single_name_fails_loud_and_keeps_every_row(
    instance: PostgresInstance,
) -> None:
    """PE15: a corrupted row is a failed step, never an invented path."""
    before = _owned_by(instance, THE_PERSON.id)
    with instance.engine.begin() as connection:
        connection.execute(
            text("UPDATE decks SET built_run = '../elsewhere' WHERE owner_id = :id"),
            {"id": THE_PERSON.id},
        )

    with pytest.raises(ValueError, match="builds root"):
        instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert _owned_by(instance, THE_PERSON.id) == before
    assert instance.build_of(THE_PERSON).exists()
    assert instance.stored(THE_PERSON)
    assert instance.stored(ANOTHER)
