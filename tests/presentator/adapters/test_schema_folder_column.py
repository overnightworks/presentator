"""The folder column of the sources table: its upgrade and a rollback past it."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

from presentator.adapters.decks import FOLDER_COLUMN
from presentator.adapters.schema import create_tables
from tests.support.deck_source_support import OWNER, a_source_database

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
