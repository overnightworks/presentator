"""A failed attempt's reason and counts in the deck table, and the upgrade to them."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.schema import create_tables
from presentator.contracts.decks import (
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
)
from tests.support.deck_source_support import COMMIT, a_build, a_deck

_TRIED_AT = datetime(2026, 1, 15, 10, tzinfo=UTC)
_SLUG = "kundenfeedback"

_EVERY_FAILURE = [
    pytest.param(BuildFailure.said("slides.md:41:3 broke"), id="toolchain"),
    pytest.param(BuildFailure.without_words(), id="no-words"),
    pytest.param(
        BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=700,
            allowed_megabytes=500,
        ),
        id="output-megabytes",
    ),
    pytest.param(
        BuildFailure(reason=BuildFailureReason.OUTPUT_ENTRIES, allowed_entries=20000),
        id="output-entries",
    ),
    pytest.param(
        BuildFailure(reason=BuildFailureReason.OUTPUT_SHAPE), id="output-shape"
    ),
    pytest.param(
        BuildFailure(
            reason=BuildFailureReason.DECK_MEGABYTES,
            allowed_megabytes=500,
            allowed_entries=20000,
        ),
        id="deck-megabytes",
    ),
    pytest.param(BuildFailure(reason=BuildFailureReason.DECK_PATHS), id="deck-paths"),
]

# The deck table as the release before this one made it.
_DECKS_BEFORE = """
CREATE TABLE decks (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    source_id TEXT NOT NULL REFERENCES sources(id),
    commit_sha TEXT NOT NULL,
    active_build TEXT,
    pdf_export TEXT,
    built_commit TEXT,
    built_at TEXT,
    attempt_commit TEXT,
    attempt_started_at TEXT,
    attempt_outcome TEXT,
    attempt_failure TEXT,
    removed_at TEXT
)
"""
_READ_BEFORE = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha, active_build,
       pdf_export, built_commit, built_at, attempt_commit, attempt_started_at,
       attempt_outcome, attempt_failure, removed_at
FROM decks WHERE removed_at IS NULL ORDER BY slug
"""
_WRITE_BEFORE = """
INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha,
    active_build, pdf_export, built_commit, built_at, attempt_commit,
    attempt_started_at, attempt_outcome, attempt_failure)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
_ROWS_BEFORE = 8
_PUT_ATTEMPT_BEFORE = """
UPDATE decks
SET attempt_commit = ?, attempt_started_at = ?,
    attempt_outcome = ?, attempt_failure = ?
WHERE slug = ?
"""
_SENTENCES_THE_SERVER_WROTE = [
    "The talk this build wrote is 700 MB; a build may leave at most 500 MB.",
    "The talk this build wrote holds more than 20000 files; a build may leave fewer.",
    "This deck cannot be built: it holds a path of more than 32 parts.",
]
_TOOLCHAIN_TEXT = "slides.md:41:3 Unexpected token in frontmatter"


def a_store(tmp_path: Path) -> SqliteDeckStore:
    database = tmp_path / "presentator.sqlite3"
    create_tables(database)
    return SqliteDeckStore(database=database)


def an_attempt(failure: BuildFailure | None) -> BuildAttempt:
    return BuildAttempt(
        commit=COMMIT,
        started_at=_TRIED_AT,
        outcome=BuildOutcome.RUNNING if failure is None else BuildOutcome.FAILED,
        failure=failure,
    )


def columns_of(database: Path) -> list[str]:
    with sqlite3.connect(database) as connection:
        found = connection.execute("SELECT name FROM pragma_table_info('decks')")
        return [name for (name,) in found]


@pytest.mark.parametrize("failure", _EVERY_FAILURE)
def test_a_failed_attempt_is_read_back_with_its_reason_and_counts(
    tmp_path: Path, failure: BuildFailure
) -> None:
    store = a_store(tmp_path)
    store.put(a_deck())

    store.put_attempt(_SLUG, an_attempt(failure))

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt == an_attempt(failure)


@pytest.mark.parametrize("failure", _EVERY_FAILURE)
def test_a_switch_over_clears_the_attempt_with_its_reason_and_counts(
    tmp_path: Path, failure: BuildFailure
) -> None:
    store = a_store(tmp_path)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(failure))

    store.put_build(_SLUG, a_build(tmp_path))

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt is None
    assert kept.build == a_build(tmp_path)

    with sqlite3.connect(store.database) as connection:
        left = connection.execute(
            "SELECT attempt_failure, attempt_reason, attempt_written_megabytes,"
            " attempt_allowed_megabytes, attempt_allowed_entries FROM decks"
        ).fetchall()
    assert left == [(None, None, None, None, None)]


def test_a_failed_attempt_without_a_reason_is_refused_loudly(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(BuildFailure.without_words()))
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE decks SET attempt_reason = NULL")

    with pytest.raises(ValueError, match="carries no reason"):
        store.get(_SLUG)


@pytest.mark.parametrize(
    "made",
    [
        lambda: BuildFailure(reason=BuildFailureReason.TOOLCHAIN),
        lambda: BuildFailure(reason=BuildFailureReason.NO_WORDS, text="words"),
        lambda: BuildFailure(reason=BuildFailureReason.OUTPUT_SHAPE, allowed_entries=1),
        lambda: BuildFailure(reason=BuildFailureReason.OUTPUT_ENTRIES),
        lambda: BuildFailure(
            reason=BuildFailureReason.DECK_MEGABYTES, allowed_megabytes=1
        ),
        lambda: BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=2,
            allowed_megabytes=1,
            allowed_entries=3,
        ),
    ],
    ids=[
        "toolchain without text",
        "no-words with text",
        "a count the reason does not name",
        "a count the reason names, missing",
        "deck-megabytes without its entries",
        "an extra count",
    ],
)
def test_a_combination_a_reason_does_not_name_cannot_be_built(
    made: Callable[[], BuildFailure],
) -> None:
    with pytest.raises(ValueError, match="cannot carry"):
        made()


def an_old_file_with_every_kind_of_row(tmp_path: Path) -> Path:
    """A file the release before this one made, with a row of every kind."""
    database = tmp_path / "presentator.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(_DECKS_BEFORE)
        old_rows = [
            ("failed-with-text", "failed", _TOOLCHAIN_TEXT),
            ("failed-without-text", "failed", None),
            ("failed-empty-text", "failed", ""),
            ("running", "running", None),
            ("built", None, None),
        ] + [
            (f"sentence-{number}", "failed", sentence)
            for number, sentence in enumerate(_SENTENCES_THE_SERVER_WROTE)
        ]
        for slug, outcome, failure in old_rows:
            connection.execute(
                _WRITE_BEFORE,
                (
                    slug,
                    slug.title(),
                    _TRIED_AT.isoformat(),
                    "the-admin",
                    "the-source",
                    COMMIT,
                    "/builds/run/out/talk" if outcome is None else None,
                    "/builds/run/out/deck.pdf" if outcome is None else None,
                    COMMIT if outcome is None else None,
                    _TRIED_AT.isoformat() if outcome is None else None,
                    None if outcome is None else COMMIT,
                    None if outcome is None else _TRIED_AT.isoformat(),
                    outcome,
                    failure,
                ),
            )
    return database


def everything_in(database: Path, statement: str) -> list[tuple[Any, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(statement).fetchall()


def test_the_upgrade_keeps_every_row_and_every_old_value(tmp_path: Path) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    before = everything_in(database, _READ_BEFORE)

    create_tables(database)

    after = everything_in(database, _READ_BEFORE)
    assert len(before) == _ROWS_BEFORE
    assert [row[:13] for row in after] == [row[:13] for row in before]
    assert [row[14] for row in after] == [row[14] for row in before]
    assert {row[0]: row[13] for row in after} == {
        row[0]: row[13] or None for row in before
    }


def test_the_upgrade_fills_the_reason_of_failed_attempts_only(tmp_path: Path) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)

    create_tables(database)

    assert dict(everything_in(database, "SELECT slug, attempt_reason FROM decks")) == {
        "failed-with-text": "toolchain",
        "failed-without-text": "no-words",
        "failed-empty-text": "no-words",
        "running": None,
        "built": None,
        "sentence-0": "toolchain",
        "sentence-1": "toolchain",
        "sentence-2": "toolchain",
    }
    assert everything_in(
        database,
        "SELECT count(*) FROM decks WHERE attempt_written_megabytes IS NOT NULL"
        " OR attempt_allowed_megabytes IS NOT NULL"
        " OR attempt_allowed_entries IS NOT NULL",
    ) == [(0,)]


def test_the_upgraded_rows_are_read_as_typed_failures(tmp_path: Path) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)

    store = SqliteDeckStore(database=database)

    failed = store.get("failed-with-text")
    silent = store.get("failed-without-text")
    assert failed is not None
    assert failed.attempt is not None
    assert failed.attempt.failure == BuildFailure.said(_TOOLCHAIN_TEXT)
    assert silent is not None
    assert silent.attempt is not None
    assert silent.attempt.failure == BuildFailure.without_words()


def test_a_second_start_changes_nothing(tmp_path: Path) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    after_the_first = everything_in(database, "SELECT * FROM decks ORDER BY slug")

    create_tables(database)

    assert (
        everything_in(database, "SELECT * FROM decks ORDER BY slug") == after_the_first
    )


def test_a_fresh_file_and_an_upgraded_one_have_the_same_columns(
    tmp_path: Path,
) -> None:
    upgraded = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(upgraded)
    fresh = tmp_path / "fresh.sqlite3"

    create_tables(fresh)

    assert sorted(columns_of(fresh)) == sorted(columns_of(upgraded))


def test_the_release_before_reads_and_writes_an_upgraded_file_as_before(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)

    read = everything_in(database, _READ_BEFORE)
    with sqlite3.connect(database) as connection:
        connection.execute(
            _PUT_ATTEMPT_BEFORE,
            (COMMIT, _TRIED_AT.isoformat(), "failed", "x", "running"),
        )

    assert len(read) == _ROWS_BEFORE
    assert everything_in(
        database,
        "SELECT attempt_outcome, attempt_failure FROM decks WHERE slug='running'",
    ) == [("failed", "x")]


def run_the_release_before(database: Path, slug: str, failure: str | None) -> None:
    """The release before this one ends a run: its statement knows no reason."""
    with sqlite3.connect(database) as connection:
        connection.execute(
            _PUT_ATTEMPT_BEFORE,
            (
                COMMIT,
                _TRIED_AT.isoformat(),
                "running" if failure is None else "failed",
                failure,
                slug,
            ),
        )


def read_failure(database: Path, slug: str) -> BuildFailure | None:
    deck = SqliteDeckStore(database=database).get(slug)
    assert deck is not None
    assert deck.attempt is not None
    return deck.attempt.failure


def test_a_failure_the_release_before_wrote_is_read_after_a_start(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    run_the_release_before(database, "running", "old release words")

    create_tables(database)

    assert read_failure(database, "running") == BuildFailure.said("old release words")


def test_a_failure_the_release_before_wrote_replaces_a_stale_server_reason(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE decks SET attempt_reason = 'output-megabytes',"
            " attempt_written_megabytes = 9, attempt_allowed_megabytes = 5"
            " WHERE slug = 'running'"
        )
    run_the_release_before(database, "running", "old release words")

    create_tables(database)

    assert read_failure(database, "running") == BuildFailure.said("old release words")


def test_a_run_the_release_before_started_carries_no_stale_reason(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE decks SET attempt_reason = 'output-megabytes',"
            " attempt_written_megabytes = 9, attempt_allowed_megabytes = 5"
            " WHERE slug IN ('running', 'built')"
        )
    run_the_release_before(database, "running", None)

    create_tables(database)

    assert read_failure(database, "running") is None
    built = SqliteDeckStore(database=database).get("built")
    assert built is not None
    assert built.attempt is None
    assert everything_in(
        database,
        "SELECT count(*) FROM decks WHERE attempt_outcome IS NOT 'failed'"
        " AND (attempt_reason IS NOT NULL OR attempt_written_megabytes IS NOT NULL)",
    ) == [(0,)]


@pytest.mark.parametrize("stored", [None, "", "slides.md:41:3 broke"])
def test_a_stored_text_is_read_as_the_failure_that_said_would_make(
    tmp_path: Path, stored: str | None
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    run_the_release_before(database, "running", "x")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE decks SET attempt_failure = ? WHERE slug = 'running'", (stored,)
        )

    create_tables(database)

    assert read_failure(database, "running") == BuildFailure.said(stored)


def test_a_second_start_changes_what_the_release_before_left_nothing_more(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    create_tables(database)
    run_the_release_before(database, "running", "old release words")
    create_tables(database)
    after_the_first = everything_in(database, "SELECT * FROM decks ORDER BY slug")

    create_tables(database)

    assert (
        everything_in(database, "SELECT * FROM decks ORDER BY slug") == after_the_first
    )


def test_a_table_missing_only_some_of_the_columns_gets_each_missing_one(
    tmp_path: Path,
) -> None:
    database = an_old_file_with_every_kind_of_row(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE decks ADD COLUMN attempt_reason TEXT")
    fresh = tmp_path / "fresh.sqlite3"
    create_tables(fresh)

    create_tables(database)

    assert sorted(columns_of(database)) == sorted(columns_of(fresh))
