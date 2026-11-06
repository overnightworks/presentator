"""A failed attempt's reason and counts in the deck table."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

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

    with closing(sqlite3.connect(store.database)) as connection, connection:
        left = connection.execute(
            "SELECT attempt_failure, attempt_reason, attempt_written_megabytes,"
            " attempt_allowed_megabytes, attempt_allowed_entries FROM decks"
        ).fetchall()
    assert left == [(None, None, None, None, None)]


def test_a_failed_attempt_without_a_reason_is_refused_loudly(tmp_path: Path) -> None:
    store = a_store(tmp_path)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(BuildFailure.without_words()))
    with closing(sqlite3.connect(store.database)) as connection, connection:
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
