"""Erasing a person over real Postgres and real mirror and build directories."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import text

from presentator.contracts.models import User
from presentator.contracts.people import PersonErased
from tests.presentator.adapters.support.person_postgres_support import (
    ANOTHER,
    THE_PERSON,
    PostgresInstance,
    an_instance_with_both_people_on_postgres,
    owned_row_keys,
    rows_still_there,
)

# What an erasure keeps of a person one of whose builds has no run: the
# account, and the source with its decks.
_KEPT = ("users", "sources", "decks")


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


@pytest.mark.parametrize(
    ("kept", "dry_run"),
    [
        pytest.param(ANOTHER, False, id="a second person's rows, mirror and build"),
        pytest.param(THE_PERSON, True, id="a dry run's rows and directories"),
    ],
)
def test_what_an_erasure_does_not_take_keeps_every_row_mirror_and_build(
    instance: PostgresInstance, kept: User, *, dry_run: bool
) -> None:
    before = _owned_by(instance, kept.id)

    instance.people.erase_person(THE_PERSON.id, dry_run=dry_run)

    assert _owned_by(instance, kept.id) == before
    assert instance.mirror_of(kept).exists()
    assert instance.build_of(kept).exists()
    assert instance.stored(kept)


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


def test_a_build_whose_run_is_no_single_name_fails_loud_and_keeps_its_source(
    instance: PostgresInstance,
) -> None:
    """PE15: a corrupted row is a failed step, never an invented path."""
    before = _owned_by(instance, THE_PERSON.id)
    with instance.engine.begin() as connection:
        connection.execute(
            text("UPDATE decks SET built_run = '../elsewhere' WHERE owner_id = :id"),
            {"id": THE_PERSON.id},
        )

    with pytest.raises(RuntimeError, match="no run can be placed"):
        instance.people.erase_person(THE_PERSON.id, dry_run=False)

    after = _owned_by(instance, THE_PERSON.id)
    assert {table: after[table] for table in _KEPT} == {
        table: before[table] for table in _KEPT
    }
    assert instance.build_of(THE_PERSON).exists()
    assert instance.stored(THE_PERSON)
    assert instance.stored(ANOTHER)
