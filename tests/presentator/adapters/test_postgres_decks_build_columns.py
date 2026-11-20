"""A deck names its build by `built_run` alone once the old columns are dropped."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from presentator.adapters.postgres.migrate import MIGRATIONS_DIRECTORY, migrate
from tests.support.postgres import created_database, engine_on

_THE_OLD_COLUMNS = {"active_build", "pdf_export"}
_A_DECK = (
    "INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha,"
    " active_build, pdf_export, built_run, built_commit, built_at)"
    " VALUES ('talk', 'A talk', now(), 'u1', 'src', 'abc123',"
    " 'builds/old', 'exports/old.pdf', 'run-7', 'abc123', now())"
)
_THE_KEPT_COLUMNS = (
    "slug, title, changed_at, owner_id, source_id, commit_sha,"
    " built_run, built_commit, built_at"
)


@pytest.fixture
def database_with_the_old_columns() -> Iterator[Engine]:
    """A database one revision before the drop, a deck holding both old columns."""
    with created_database() as database_url:
        engine = engine_on(database_url)
        try:
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0005")
                connection.execute(
                    text(
                        "INSERT INTO users (id, username, role)"
                        " VALUES ('u1', 'ada', 'admin')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO sources (id, name, url, ref, encrypted_secret,"
                        " owner_id) VALUES ('src', 'n', 'u', 'main', ''::bytea, 'u1')"
                    )
                )
                connection.execute(text(_A_DECK))
            yield engine
        finally:
            engine.dispose()


def _deck_columns(engine: Engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("decks")}


def _the_kept_values(engine: Engine) -> list[tuple[object, ...]]:
    with engine.connect() as connection:
        rows = connection.execute(text(f"SELECT {_THE_KEPT_COLUMNS} FROM decks"))  # noqa: S608 - fixed names
        return [tuple(row) for row in rows]


def test_the_database_before_the_drop_holds_both_old_columns(
    database_with_the_old_columns: Engine,
) -> None:
    assert _deck_columns(database_with_the_old_columns) >= _THE_OLD_COLUMNS


def test_migrating_drops_both_old_columns_and_keeps_every_other_value(
    database_with_the_old_columns: Engine,
) -> None:
    kept_before = _the_kept_values(database_with_the_old_columns)
    columns_before = _deck_columns(database_with_the_old_columns)

    migrate(database_with_the_old_columns)

    assert _deck_columns(database_with_the_old_columns) == (
        columns_before - _THE_OLD_COLUMNS
    )
    assert _the_kept_values(database_with_the_old_columns) == kept_before
    assert kept_before[0][6] == "run-7"


def test_a_second_migration_after_the_drop_does_nothing(
    database_with_the_old_columns: Engine,
) -> None:
    migrate(database_with_the_old_columns)
    columns = _deck_columns(database_with_the_old_columns)
    kept = _the_kept_values(database_with_the_old_columns)

    migrate(database_with_the_old_columns)

    assert _deck_columns(database_with_the_old_columns) == columns
    assert _the_kept_values(database_with_the_old_columns) == kept
