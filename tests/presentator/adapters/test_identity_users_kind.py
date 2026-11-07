"""The kind of an account, in the users table and its start-up upgrade."""

import sqlite3
import threading
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from webauth.ports import AccountKind

from presentator.adapters.decks import create_deck_tables
from presentator.adapters.identity import create_identity_tables
from presentator.adapters.preferences import create_preference_tables
from presentator.adapters.schema import create_tables

_THE_RELEASE_BEFORE = """
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL
);
INSERT INTO users (id, username, role) VALUES
    ('subject-1', 'felix', 'admin'),
    ('subject-2', 'anna', 'user');
"""
_LONG_ENOUGH_TO_REACH_THE_LOCK = 10
_THE_WAITING_START = "the-waiting-start"
_KINDS = "SELECT id, kind FROM users ORDER BY id"


def kinds(database: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute(_KINDS).fetchall()


@pytest.fixture
def before_the_upgrade(tmp_path: Path) -> Path:
    database = tmp_path / "presentator.sqlite3"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(_THE_RELEASE_BEFORE)
    return database


def test_a_start_reads_every_row_the_release_before_wrote_as_a_person(
    before_the_upgrade: Path,
) -> None:
    create_tables(before_the_upgrade)

    assert kinds(before_the_upgrade) == [
        ("subject-1", AccountKind.PERSON.value),
        ("subject-2", AccountKind.PERSON.value),
    ]


def test_a_second_start_changes_nothing(before_the_upgrade: Path) -> None:
    create_tables(before_the_upgrade)
    with closing(sqlite3.connect(before_the_upgrade)) as connection:
        connection.execute("UPDATE users SET kind = 'agent' WHERE id = 'subject-2'")
        connection.commit()
    first = kinds(before_the_upgrade)

    create_tables(before_the_upgrade)

    assert kinds(before_the_upgrade) == first


def test_a_start_waiting_on_another_start_adds_no_second_kind_column(
    before_the_upgrade: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_identity_tables(before_the_upgrade)
    create_preference_tables(before_the_upgrade)
    create_deck_tables(before_the_upgrade)
    refusals: list[BaseException] = []
    reached_the_lock = threading.Event()
    connect = sqlite3.connect

    def watching_connect(
        database: Path, isolation_level: Literal["DEFERRED"] | None = "DEFERRED"
    ) -> sqlite3.Connection:
        connection = connect(database, isolation_level=isolation_level)
        if threading.current_thread().name == _THE_WAITING_START:
            connection.set_trace_callback(
                lambda statement: (
                    reached_the_lock.set() if statement == "BEGIN IMMEDIATE" else None
                )
            )
        return connection

    def start() -> None:
        try:
            create_tables(before_the_upgrade)
        except BaseException as refused:  # noqa: BLE001 - the test reports whatever a start raised
            refusals.append(refused)

    with closing(connect(before_the_upgrade, isolation_level=None)) as holder:
        holder.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(sqlite3, "connect", watching_connect)
        waiting = threading.Thread(target=start, name=_THE_WAITING_START)
        waiting.start()
        assert reached_the_lock.wait(timeout=_LONG_ENOUGH_TO_REACH_THE_LOCK)
        holder.execute(
            "ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'person'"
        )
        holder.execute("COMMIT")
    waiting.join()

    assert refusals == []
    assert {kind for _, kind in kinds(before_the_upgrade)} == {AccountKind.PERSON.value}


def _insert_without_the_column(database: Path) -> None:
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "INSERT INTO users (id, username, role) VALUES ('subject-3', 'ben', 'user')"
        )
        connection.commit()


def test_an_insert_without_the_column_into_a_fresh_file_reads_as_a_person(
    tmp_path: Path,
) -> None:
    database = tmp_path / "presentator.sqlite3"
    create_identity_tables(database)

    _insert_without_the_column(database)

    assert kinds(database) == [("subject-3", AccountKind.PERSON.value)]


def test_an_insert_without_the_column_into_an_upgraded_file_reads_as_a_person(
    before_the_upgrade: Path,
) -> None:
    create_tables(before_the_upgrade)

    _insert_without_the_column(before_the_upgrade)

    assert dict(kinds(before_the_upgrade))["subject-3"] == AccountKind.PERSON.value


def test_a_fresh_file_refuses_a_kind_other_than_the_two(tmp_path: Path) -> None:
    database = tmp_path / "presentator.sqlite3"
    create_identity_tables(database)

    with (
        closing(sqlite3.connect(database)) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute(
            "INSERT INTO users (id, username, role, kind)"
            " VALUES ('subject-9', 'zed', 'user', 'robot')"
        )


def test_an_upgraded_file_refuses_a_kind_other_than_the_two(
    before_the_upgrade: Path,
) -> None:
    create_tables(before_the_upgrade)

    with (
        closing(sqlite3.connect(before_the_upgrade)) as connection,
        pytest.raises(sqlite3.IntegrityError),
    ):
        connection.execute("UPDATE users SET kind = 'robot' WHERE id = 'subject-1'")
