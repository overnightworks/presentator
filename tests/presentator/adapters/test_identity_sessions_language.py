"""The account language of a session, in the sessions table and its start-up upgrade."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.adapters.identity import SqliteSessionRecordStore
from presentator.adapters.schema import create_tables

_NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)
_THE_RELEASE_BEFORE = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'person'
);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    refresh_token TEXT,
    id_token TEXT
);
INSERT INTO users (id, username, role) VALUES ('subject-1', 'felix', 'admin');
INSERT INTO sessions (id, user_id, created_at, expires_at)
VALUES ('old-session', 'subject-1', '2026-01-15T09:00:00+00:00',
        '2026-01-15T10:00:00+00:00');
"""
_THE_RELEASE_BEFORES_INSERT = """
INSERT INTO sessions (id, user_id, created_at, expires_at, refresh_token, id_token)
VALUES ('rolled-back', 'subject-1', '2026-01-15T09:00:00+00:00',
        '2026-01-15T10:00:00+00:00', NULL, NULL)
"""


def a_release_before_file(tmp_path: Path) -> Path:
    database = tmp_path / "before.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(_THE_RELEASE_BEFORE)
    return database


def a_fresh_file(tmp_path: Path) -> Path:
    database = tmp_path / "fresh.sqlite3"
    create_tables(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO users (id, username, role)"
            " VALUES ('subject-1', 'felix', 'admin')"
        )
        connection.commit()
    return database


def columns_of_sessions(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return [
            name
            for (name,) in connection.execute(
                "SELECT name FROM pragma_table_info('sessions')"
            )
        ]


def test_a_start_reads_a_session_of_the_release_before_as_naming_no_language(
    tmp_path: Path,
) -> None:
    database = a_release_before_file(tmp_path)

    create_tables(database)

    old = SqliteSessionRecordStore(database).load("old-session")
    assert old is not None
    assert old.account_language is None


def test_a_second_start_adds_no_column_and_keeps_a_stored_language(
    tmp_path: Path,
) -> None:
    database = a_release_before_file(tmp_path)
    create_tables(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("UPDATE sessions SET account_language = 'de'")
        connection.commit()

    create_tables(database)

    kept = SqliteSessionRecordStore(database).load("old-session")
    assert kept is not None
    assert kept.account_language == "de"
    assert columns_of_sessions(database).count("account_language") == 1


@pytest.mark.parametrize(
    "starting_file",
    [a_release_before_file, a_fresh_file],
    ids=["upgraded", "fresh"],
)
def test_the_release_before_inserts_a_session_row_without_the_language_after_a_rollback(
    tmp_path: Path, starting_file: Callable[[Path], Path]
) -> None:
    database = starting_file(tmp_path)
    create_tables(database)

    with closing(sqlite3.connect(database)) as connection:
        connection.execute(_THE_RELEASE_BEFORES_INSERT)
        connection.commit()

    rolled_back = SqliteSessionRecordStore(database).load("rolled-back")
    assert rolled_back is not None
    assert rolled_back.account_language is None


def test_a_new_file_and_an_upgraded_one_hold_the_same_session_columns(
    tmp_path: Path,
) -> None:
    (tmp_path / "before").mkdir()
    (tmp_path / "fresh").mkdir()
    upgraded = a_release_before_file(tmp_path / "before")
    create_tables(upgraded)
    fresh = a_fresh_file(tmp_path / "fresh")

    assert sorted(columns_of_sessions(fresh)) == sorted(columns_of_sessions(upgraded))
