"""A deck names its build by `built_run` alone once the old columns are dropped."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text

from presentator.adapters.postgres.migrate import migrate
from tests.support.postgres import database_at

_THE_OLD_COLUMNS = {"active_build", "pdf_export"}
_A_DECK = (
    "INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha,"
    " active_build, pdf_export, built_run, built_commit, built_at,"
    " attempt_commit, attempt_started_at, attempt_outcome, attempt_failure,"
    " attempt_reason, attempt_written_megabytes, attempt_allowed_megabytes,"
    " attempt_allowed_entries, built_toolchain, attempt_toolchain, removed_at)"
    " VALUES ('talk', 'A talk', '2026-01-02 03:04:05+00', 'u1', 'src', 'abc123',"
    " 'builds/old', 'exports/old.pdf', 'run-7', 'abc123', '2026-01-02 03:05:06+00',"
    " 'def456', '2026-01-03 04:05:06+00', 'failed', 'too_large',"
    " 'over the bound', 11, 10, 12, 'node-22', 'node-24', '2026-01-04 05:06:07+00')"
)


@pytest.fixture
def database_with_the_old_columns() -> Iterator[Engine]:
    """A database one revision before the drop, a deck holding both old columns."""
    with database_at(
        "0005",
        rows=(
            "INSERT INTO users (id, username, role) VALUES ('u1', 'ada', 'admin')",
            (
                "INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)"
                " VALUES ('src', 'n', 'u', 'main', ''::bytea, 'u1')"
            ),
            _A_DECK,
        ),
    ) as engine:
        yield engine


def _deck_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("decks")}


def _deck_rows(engine: Engine) -> list[dict[str, object]]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT * FROM decks")).mappings().all()
        return [dict(row) for row in rows]


def _without_the_old_columns(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {name: value for name, value in row.items() if name not in _THE_OLD_COLUMNS}
        for row in rows
    ]


def test_migrating_drops_both_old_columns_and_keeps_every_other_value(
    database_with_the_old_columns: Engine,
) -> None:
    kept_before = _without_the_old_columns(_deck_rows(database_with_the_old_columns))
    columns_before = _deck_columns(database_with_the_old_columns)

    migrate(database_with_the_old_columns)

    assert _deck_columns(database_with_the_old_columns) == (
        columns_before - _THE_OLD_COLUMNS
    )
    assert _deck_rows(database_with_the_old_columns) == kept_before
    assert all(value is not None for value in kept_before[0].values())


def test_a_second_migration_after_the_drop_does_nothing(
    database_with_the_old_columns: Engine,
) -> None:
    migrate(database_with_the_old_columns)
    columns = _deck_columns(database_with_the_old_columns)
    rows = _deck_rows(database_with_the_old_columns)

    migrate(database_with_the_old_columns)

    assert _deck_columns(database_with_the_old_columns) == columns
    assert _deck_rows(database_with_the_old_columns) == rows
