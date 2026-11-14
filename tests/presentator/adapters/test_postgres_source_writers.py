"""Two processes writing the same source rows at once keep their guarantees."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.secrets import secret_box
from tests.presentator.adapters.support.source_stores_support import (
    INSTANCE_KEY,
    seed_postgres_owner,
)
from tests.presentator.adapters.support.source_writer_process import OWNERS
from tests.support.postgres import database_environment, engine_on
from tests.support.two_processes import (
    Finished,
    release_together,
    two_modules,
)

_NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
_CHILD = "tests.presentator.adapters.support.source_writer_process"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


@pytest.fixture
def owners(engine: Engine) -> tuple[str, ...]:
    for owner in OWNERS:
        seed_postgres_owner(engine, owner)
    return OWNERS


def _both_write[Reported](
    action: str, database_url: str, directory: Path, shape: TypeAdapter[Reported]
) -> tuple[Reported, Reported]:
    """What each of two processes doing `action` at once reported, both exiting 0."""
    environment = database_environment(database_url, directory)
    with two_modules(_CHILD, [action], environment) as processes:
        release_together(processes)
        finished = processes.finish()
    assert [each.exit_code for each in finished] == [0, 0], finished
    first, second = (_reported(each, shape) for each in finished)
    return first, second


def _reported[Reported](finished: Finished, shape: TypeAdapter[Reported]) -> Reported:
    return shape.validate_json(finished.output.strip().splitlines()[-1])


def _drafts_per_owner(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT owner_id, COUNT(*) FROM source_key_drafts GROUP BY owner_id")
        ).all()
    return dict(found)


def test_two_processes_opening_add_at_once_for_one_owner_get_one_draft(
    database_url: str, tmp_path: Path, engine: Engine, owners: tuple[str, ...]
) -> None:
    first, second = _both_write(
        "get-or-mint", database_url, tmp_path, TypeAdapter(dict[str, str])
    )

    assert first == second
    assert _drafts_per_owner(engine) == dict.fromkeys(owners, 1)


def test_two_processes_replacing_the_same_drafts_in_place_leave_one_each(
    database_url: str, tmp_path: Path, engine: Engine, owners: tuple[str, ...]
) -> None:
    first, second = _both_write(
        "mint", database_url, tmp_path, TypeAdapter(dict[str, list[str]])
    )

    with engine.connect() as connection:
        kept = dict(
            connection.execute(text("SELECT owner_id, id FROM source_key_drafts")).all()
        )
    assert sorted(kept) == sorted(owners)
    for owner in owners:
        assert kept[owner] in {*first[owner], *second[owner]}


def test_two_processes_binding_the_same_drafts_consume_each_once(
    database_url: str, tmp_path: Path, engine: Engine, owners: tuple[str, ...]
) -> None:
    drafts = PostgresSourceKeyDrafts(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
    )
    for owner in owners:
        drafts.get_or_mint(owner, newer_than=_NOW - timedelta(days=1), at=_NOW)

    first, second = _both_write("bind", database_url, tmp_path, TypeAdapter(list[str]))

    assert sorted([*first, *second]) == sorted(owners)
    assert _drafts_per_owner(engine) == {}
