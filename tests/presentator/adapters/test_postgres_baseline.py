"""The migrated database holds the tables of the store a started instance has today."""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import Connection, inspect, text
from sqlalchemy.exc import IntegrityError

from presentator.adapters.schema import create_tables
from tests.support.postgres import engine_on

# `refresh_owner` fences the refresh of several processes, which one file never has.
_HAS_NO_SQLITE_COUNTERPART = {"alembic_version", "refresh_owner"}


# Whether the column refuses NULL, its Postgres type and its default.
_Column = tuple[bool, str, str | None]


def _is_an_instant(column: str) -> bool:
    """SQLite keeps an instant as text; its columns are named `at` or `*_at`."""
    return column == "at" or column.endswith("_at")


def _postgres_type_of(column: str, sqlite_type: str) -> str:
    """The Postgres type that keeps every value of the SQLite declaration."""
    if _is_an_instant(column):
        return "TIMESTAMP WITH TIME ZONE"
    return {"TEXT": "TEXT", "BLOB": "BYTEA", "INTEGER": "BIGINT"}[sqlite_type]


def _sqlite_shape(database: Path) -> dict[str, dict[str, _Column]]:
    """Each table's columns as Postgres is to hold them.

    SQLite lets a text key hold NULL unless told not to, which no row relies on;
    Postgres never does, so a key counts as refusing it.
    """
    create_tables(database)
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        columns = {
            table: connection.execute(
                "SELECT * FROM pragma_table_info(?)", (table,)
            ).fetchall()
            for table in tables
        }
    return {
        table: {
            name: (
                bool(not_null or in_key),
                _postgres_type_of(name, sqlite_type),
                default,
            )
            for _, name, sqlite_type, not_null, default, in_key in rows
        }
        for table, rows in columns.items()
    }


def _postgres_default(default: str | None) -> str | None:
    """A default as SQLite writes it: Postgres adds the cast of the literal."""
    return None if default is None else default.removesuffix("::text")


def _sqlite_references(database: Path) -> set[tuple[str, str, str, str]]:
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            name for (name,) in connection.execute("SELECT name FROM pragma_table_list")
        ]
        return {
            (table, column, referred, referred_column)
            for table in tables
            for _, _, referred, column, referred_column, *_ in connection.execute(
                "SELECT * FROM pragma_foreign_key_list(?)", (table,)
            )
        }


def _sqlite_unique_columns(database: Path) -> set[tuple[str, tuple[str, ...]]]:
    """Each table's primary key and unique constraints, as the columns they hold."""
    with closing(sqlite3.connect(database)) as connection:
        tables = [
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_schema"
                " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        unique: set[tuple[str, tuple[str, ...]]] = set()
        for table in tables:
            primary = tuple(
                name
                for _, name, *_, position in sorted(
                    connection.execute("SELECT * FROM pragma_table_info(?)", (table,)),
                    key=lambda column: column[5],
                )
                if position
            )
            unique.add((table, primary))
            for (index,) in connection.execute(
                "SELECT name FROM pragma_index_list(?)"
                " WHERE \"unique\" AND origin = 'u'",
                (table,),
            ):
                columns = tuple(
                    name
                    for (name,) in connection.execute(
                        "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index,)
                    )
                )
                unique.add((table, columns))
        return unique


@pytest.fixture
def sqlite_database(tmp_path: Path) -> Path:
    database = tmp_path / "store.sqlite3"
    _sqlite_shape(database)
    return database


def test_a_migrated_database_has_the_columns_types_and_defaults_of_the_sqlite_store(
    database_url: str, sqlite_database: Path
) -> None:
    expected = _sqlite_shape(sqlite_database)

    engine = engine_on(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names()) - _HAS_NO_SQLITE_COUNTERPART
        shape = {
            table: {
                column["name"]: (
                    not column["nullable"],
                    column["type"].compile(dialect=engine.dialect),
                    _postgres_default(column["default"]),
                )
                for column in inspector.get_columns(table)
            }
            for table in tables
        }
    finally:
        engine.dispose()

    assert shape == expected


def test_a_migrated_database_refers_between_tables_as_the_sqlite_store_does(
    database_url: str, sqlite_database: Path
) -> None:
    engine = engine_on(database_url)
    try:
        inspector = inspect(engine)
        references = {
            (table, column, key["referred_table"], referred)
            for table in inspector.get_table_names()
            for key in inspector.get_foreign_keys(table)
            for column, referred in zip(
                key["constrained_columns"], key["referred_columns"], strict=True
            )
        }
    finally:
        engine.dispose()

    assert references == _sqlite_references(sqlite_database)


def test_a_migrated_database_keeps_one_row_per_key_as_the_sqlite_store_does(
    database_url: str, sqlite_database: Path
) -> None:
    engine = engine_on(database_url)
    try:
        inspector = inspect(engine)
        unique: set[tuple[str, tuple[str, ...]]] = set()
        for table in set(inspector.get_table_names()) - _HAS_NO_SQLITE_COUNTERPART:
            primary = inspector.get_pk_constraint(table)["constrained_columns"]
            unique.add((table, tuple(primary)))
            unique.update(
                (table, tuple(constraint["column_names"]))
                for constraint in inspector.get_unique_constraints(table)
            )
    finally:
        engine.dispose()

    assert unique == _sqlite_unique_columns(sqlite_database)


_A_USER = "INSERT INTO users (id, username, role) VALUES ('u1', 'ada', 'admin')"


@pytest.fixture
def connection(database_url: str) -> Iterator[Connection]:
    engine = engine_on(database_url)
    try:
        with engine.begin() as open_connection:
            yield open_connection
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param(
            "INSERT INTO users (id, username, role, kind)"
            " VALUES ('u2', 'bo', 'x', 'robot')",
            id="a kind that is neither a person nor an agent",
        ),
        pytest.param(
            "INSERT INTO instance_settings (id, name) VALUES (2, 'second')",
            id="a second row of instance settings",
        ),
        pytest.param(
            "INSERT INTO sessions (id, user_id, created_at, expires_at)"
            " VALUES ('s1', 'nobody', now(), now())",
            id="a session of a user that does not exist",
        ),
    ],
)
def test_a_migrated_database_refuses(connection: Connection, refused: str) -> None:
    connection.execute(text(_A_USER))

    with pytest.raises(IntegrityError):
        connection.execute(text(refused))


def test_a_user_without_a_kind_is_a_person(connection: Connection) -> None:
    connection.execute(text(_A_USER))

    kind = connection.execute(text("SELECT kind FROM users")).scalar_one()

    assert kind == "person"


def test_a_run_of_a_source_gets_the_next_number(connection: Connection) -> None:
    connection.execute(text(_A_USER))
    connection.execute(
        text(
            "INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)"
            " VALUES ('src', 'n', 'u', 'main', ''::bytea, 'u1')"
        )
    )
    run = "INSERT INTO source_runs (source_id, at, outcome) VALUES ('src', now(), 'ok')"

    connection.execute(text(run))
    connection.execute(text(run))

    numbers = connection.execute(text("SELECT id FROM source_runs ORDER BY id"))
    assert [number for (number,) in numbers] == [1, 2]
