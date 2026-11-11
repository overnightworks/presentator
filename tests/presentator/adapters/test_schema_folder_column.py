"""The columns the schema adds to an older file, and a rollback past them."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import FOLDER_COLUMN
from presentator.adapters.schema import create_tables
from presentator.contracts.decks import ToolchainIdentity
from tests.support.deck_source_support import (
    COMMIT,
    OWNER,
    SOURCE_ID,
    a_build,
    a_deck,
    a_source_database,
)

_THE_OLDER_TABLE = """
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL UNIQUE,
    ref TEXT NOT NULL,
    encrypted_secret BLOB NOT NULL,
    hook_secret_hash BLOB,
    owner_id TEXT NOT NULL REFERENCES users(id),
    ssh_public_key TEXT
);
"""
_AN_OLDER_ROW = """
INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)
VALUES ('old', 'old-source', 'https://example.invalid/old.git', 'main', x'00', ?)
"""
_THE_OLDER_RELEASES_INSERT = """
INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)
VALUES ('rolled', 'rolled-back', 'https://example.invalid/rolled.git',
        'main', x'00', ?)
"""
_FOLDERS = "SELECT name, folder FROM sources ORDER BY name"


def folders_stored(database: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(_FOLDERS).fetchall()


def columns_of_sources(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return [
            name
            for (name,) in connection.execute(
                "SELECT name FROM pragma_table_info('sources')"
            )
        ]


def an_older_release_file(tmp_path: Path) -> Path:
    """A file as the release before left it: sources without a folder column."""
    database = a_source_database(tmp_path)
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript("DROP TABLE sources;" + _THE_OLDER_TABLE)
        connection.execute(_AN_OLDER_ROW, (OWNER.id,))
        connection.commit()
    return database


@pytest.fixture
def before_the_upgrade(tmp_path: Path) -> Path:
    return an_older_release_file(tmp_path)


def test_a_start_reads_an_existing_source_as_naming_no_folder(
    before_the_upgrade: Path,
) -> None:
    create_tables(before_the_upgrade)

    assert folders_stored(before_the_upgrade) == [("old-source", "")]


def test_a_second_start_adds_no_column_and_keeps_a_stored_folder(
    before_the_upgrade: Path,
) -> None:
    create_tables(before_the_upgrade)
    with closing(sqlite3.connect(before_the_upgrade)) as connection:
        connection.execute("UPDATE sources SET folder = 'examples'")
        connection.commit()

    create_tables(before_the_upgrade)

    assert folders_stored(before_the_upgrade) == [("old-source", "examples")]
    assert columns_of_sources(before_the_upgrade).count("folder") == 1


@pytest.mark.parametrize(
    "starting_file",
    [an_older_release_file, a_source_database],
    ids=["upgraded", "fresh"],
)
def test_the_older_releases_insert_after_a_rollback_writes_a_row_with_no_folder(
    tmp_path: Path, starting_file: Callable[[Path], Path]
) -> None:
    database = starting_file(tmp_path)
    create_tables(database)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(_THE_OLDER_RELEASES_INSERT, (OWNER.id,))
        connection.commit()

    assert ("rolled-back", "") in folders_stored(database)


def test_a_new_file_and_an_upgraded_one_hold_the_same_column(
    before_the_upgrade: Path, tmp_path: Path
) -> None:
    create_tables(before_the_upgrade)
    fresh = tmp_path / "fresh.sqlite3"
    create_tables(fresh)

    assert columns_of_sources(fresh) == columns_of_sources(before_the_upgrade)
    assert FOLDER_COLUMN.split()[0] == columns_of_sources(fresh)[-1]


_THE_TOOLCHAIN_COLUMNS = ("built_toolchain", "attempt_toolchain")
_THE_OLDER_RELEASES_DECK_INSERT = """
INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha)
VALUES ('rolled', 'Rolled back', '2026-01-15T09:00:00+00:00', ?, ?, ?)
"""
_THE_OLDER_RELEASES_BUILD_WRITE = """
UPDATE decks
SET active_build = '/builds/talk', pdf_export = '/builds/deck.pdf',
    built_commit = ?, built_at = '2026-01-15T09:30:00+00:00',
    attempt_commit = NULL, attempt_started_at = NULL, attempt_outcome = NULL
WHERE slug = 'rolled'
"""
_THE_OLDER_RELEASES_DECK_READ = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha, active_build,
       pdf_export, built_commit, built_at, attempt_commit, attempt_started_at,
       attempt_outcome, attempt_failure, attempt_reason,
       attempt_written_megabytes, attempt_allowed_megabytes, attempt_allowed_entries
FROM decks WHERE removed_at IS NULL
"""


def columns_of_decks(database: Path) -> list[str]:
    with closing(sqlite3.connect(database)) as connection:
        return [
            name
            for (name,) in connection.execute(
                "SELECT name FROM pragma_table_info('decks')"
            )
        ]


def a_release_before_the_toolchain_columns(tmp_path: Path) -> Path:
    """A file as the release before left it, with one standing talk."""
    database = a_source_database(tmp_path)
    store = SqliteDeckStore(database=database)
    store.put(a_deck())
    store.put_build(
        "kundenfeedback",
        replace(a_build(tmp_path), toolchain=ToolchainIdentity("a" * 64)),
    )
    with closing(sqlite3.connect(database)) as connection:
        for column in _THE_TOOLCHAIN_COLUMNS:
            connection.execute("ALTER TABLE decks DROP COLUMN " + column)
        connection.commit()
    return database


def test_a_start_gives_a_deck_of_the_release_before_the_columns_and_no_toolchain(
    tmp_path: Path,
) -> None:
    database = a_release_before_the_toolchain_columns(tmp_path)

    create_tables(database)
    create_tables(database)

    assert [
        columns_of_decks(database).count(column) for column in _THE_TOOLCHAIN_COLUMNS
    ] == [1, 1]
    kept = SqliteDeckStore(database=database).get("kundenfeedback")
    assert kept is not None
    assert kept.build is not None
    assert (kept.build.commit, kept.build.toolchain) == (COMMIT, None)


@pytest.mark.parametrize(
    "starting_file",
    [a_release_before_the_toolchain_columns, a_source_database],
    ids=["upgraded", "fresh"],
)
def test_the_older_release_writes_and_reads_deck_rows_on_the_newer_file(
    tmp_path: Path, starting_file: Callable[[Path], Path]
) -> None:
    database = starting_file(tmp_path)
    create_tables(database)

    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            _THE_OLDER_RELEASES_DECK_INSERT, (OWNER.id, SOURCE_ID, COMMIT)
        )
        connection.execute(_THE_OLDER_RELEASES_BUILD_WRITE, (COMMIT,))
        connection.commit()
        read = connection.execute(_THE_OLDER_RELEASES_DECK_READ).fetchall()

    assert "rolled" in [row[0] for row in read]
    kept = SqliteDeckStore(database=database).get("rolled")
    assert kept is not None
    assert kept.build is not None
    assert kept.build.toolchain is None


def test_a_new_file_and_an_upgraded_one_hold_the_same_deck_columns(
    tmp_path: Path,
) -> None:
    (tmp_path / "before").mkdir()
    (tmp_path / "fresh").mkdir()
    upgraded = a_release_before_the_toolchain_columns(tmp_path / "before")
    create_tables(upgraded)
    fresh = a_source_database(tmp_path / "fresh")
    create_tables(fresh)

    assert sorted(columns_of_decks(fresh)) == sorted(columns_of_decks(upgraded))
