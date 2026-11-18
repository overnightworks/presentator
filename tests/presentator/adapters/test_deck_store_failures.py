"""What the deck table keeps of an attempt, a build and the toolchain behind them."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.application.deck_builds import DeckBuilds
from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    ToolchainIdentity,
)
from tests.presentator.application.support.fakes import FakeBuildRunner, FrozenClock
from tests.support.deck_source_support import (
    BUILT_AT,
    COMMIT,
    a_build,
    a_deck,
    a_source,
    seed_source_rows,
)
from tests.support.postgres import NO_FENCE

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
    pytest.param(BuildFailure(reason=BuildFailureReason.STORE), id="store"),
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


def a_store(engine: Engine) -> PostgresDeckStore:
    return PostgresDeckStore(
        engine=seed_source_rows(engine, a_deck().source_id), fence=NO_FENCE
    )


def an_attempt(failure: BuildFailure | None) -> BuildAttempt:
    return BuildAttempt(
        commit=COMMIT,
        started_at=_TRIED_AT,
        outcome=BuildOutcome.RUNNING if failure is None else BuildOutcome.FAILED,
        failure=failure,
        toolchain=None,
    )


@pytest.mark.parametrize("failure", _EVERY_FAILURE)
def test_a_failed_attempt_is_read_back_with_its_reason_and_counts(
    engine: Engine, failure: BuildFailure
) -> None:
    store = a_store(engine)
    store.put(a_deck())

    store.put_attempt(_SLUG, an_attempt(failure))

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt == an_attempt(failure)


@pytest.mark.parametrize("failure", _EVERY_FAILURE)
def test_putting_no_attempt_back_clears_every_attempt_column(
    engine: Engine, failure: BuildFailure
) -> None:
    store = a_store(engine)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(failure))

    store.put_attempt(_SLUG, None)

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt is None
    with store.engine.begin() as connection:
        left = connection.exec_driver_sql(
            "SELECT attempt_commit, attempt_started_at, attempt_outcome,"
            " attempt_failure, attempt_reason, attempt_written_megabytes,"
            " attempt_allowed_megabytes, attempt_allowed_entries FROM decks"
        ).fetchall()
    assert left == [(None,) * 8]


@pytest.mark.parametrize("failure", _EVERY_FAILURE)
def test_a_switch_over_clears_the_attempt_with_its_reason_and_counts(
    tmp_path: Path, engine: Engine, failure: BuildFailure
) -> None:
    store = a_store(engine)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(failure))

    store.put_build(_SLUG, a_build(tmp_path))

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt is None
    assert kept.build == a_build(tmp_path)

    with store.engine.begin() as connection:
        left = connection.exec_driver_sql(
            "SELECT attempt_failure, attempt_reason, attempt_written_megabytes,"
            " attempt_allowed_megabytes, attempt_allowed_entries FROM decks"
        ).fetchall()
    assert left == [(None, None, None, None, None)]


def test_a_failed_attempt_without_a_reason_is_refused_loudly(engine: Engine) -> None:
    store = a_store(engine)
    store.put(a_deck())
    store.put_attempt(_SLUG, an_attempt(BuildFailure.without_words()))
    with store.engine.begin() as connection:
        connection.exec_driver_sql("UPDATE decks SET attempt_reason = NULL")

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


_A_TOOLCHAIN = ToolchainIdentity("a" * 64)
_ANOTHER_TOOLCHAIN = ToolchainIdentity("b" * 64)
_ANOTHER_COMMIT = "b7c1d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f80"


def a_store_after_a_build_and_a_failed_attempt(
    tmp_path: Path, engine: Engine
) -> PostgresDeckStore:
    store = a_store(engine)
    store.put(a_deck())
    store.put_build(_SLUG, replace(a_build(tmp_path), toolchain=_A_TOOLCHAIN))
    store.put_attempt(
        _SLUG,
        replace(an_attempt(BuildFailure.without_words()), toolchain=_ANOTHER_TOOLCHAIN),
    )
    return store


def test_a_build_and_an_attempt_are_read_back_with_the_toolchain_they_ran_under(
    tmp_path: Path,
    engine: Engine,
) -> None:
    store = a_store_after_a_build_and_a_failed_attempt(tmp_path, engine)

    kept = store.get(_SLUG)

    assert kept is not None
    assert kept.build is not None
    assert kept.build.toolchain == _A_TOOLCHAIN
    assert kept.attempt is not None
    assert kept.attempt.toolchain == _ANOTHER_TOOLCHAIN


def test_a_switch_over_takes_the_attempts_toolchain_away_with_the_attempt(
    tmp_path: Path,
    engine: Engine,
) -> None:
    store = a_store_after_a_build_and_a_failed_attempt(tmp_path, engine)

    store.put_build(_SLUG, replace(a_build(tmp_path), toolchain=_ANOTHER_TOOLCHAIN))

    kept = store.get(_SLUG)
    assert kept is not None
    assert kept.attempt is None
    assert kept.build is not None
    assert kept.build.toolchain == _ANOTHER_TOOLCHAIN
    with store.engine.begin() as connection:
        assert connection.exec_driver_sql(
            "SELECT attempt_toolchain FROM decks"
        ).fetchall() == [(None,)]


_READ_WHAT_IS_STORED = {
    "build": "SELECT built_toolchain FROM decks",
    "attempt": "SELECT attempt_toolchain FROM decks",
}
_WRITE_WHAT_IS_STORED = {
    "build": "UPDATE decks SET built_toolchain = %s",
    "attempt": "UPDATE decks SET attempt_toolchain = %s",
}
_THE_COMMIT_IT_IS_FOR = {
    "build": "UPDATE decks SET built_commit = %s",
    "attempt": "UPDATE decks SET attempt_commit = %s",
}
_STORED_WITH_A_FLAW: dict[str, Callable[[str], str | None]] = {
    "a row from before": lambda _stored: None,
    "a malformed value": lambda _stored: "not an identity",
    "a value without its commit": lambda stored: stored[-64:],
    "digits that are not hex": lambda stored: stored[:-64] + "g" * 64,
}


def held_by(store: PostgresDeckStore, which: str) -> BuildAttempt | Build:
    kept = store.get(_SLUG)
    assert kept is not None
    held = kept.build if which == "build" else kept.attempt
    assert held is not None
    return held


@pytest.mark.parametrize("which", ["build", "attempt"])
@pytest.mark.parametrize("flaw", _STORED_WITH_A_FLAW)
def test_a_toolchain_the_row_cannot_vouch_for_reads_as_unknown(
    tmp_path: Path, engine: Engine, flaw: str, which: str
) -> None:
    store = a_store_after_a_build_and_a_failed_attempt(tmp_path, engine)
    with store.engine.begin() as connection:
        stored = connection.exec_driver_sql(_READ_WHAT_IS_STORED[which]).scalar_one()
        flawed = _STORED_WITH_A_FLAW[flaw](stored)
        connection.exec_driver_sql(_WRITE_WHAT_IS_STORED[which], (flawed,))

    assert held_by(store, which).toolchain is None


@pytest.mark.parametrize("which", ["build", "attempt"])
def test_a_toolchain_written_for_another_commit_reads_as_unknown(
    tmp_path: Path, engine: Engine, which: str
) -> None:
    store = a_store_after_a_build_and_a_failed_attempt(tmp_path, engine)
    with store.engine.begin() as connection:
        connection.exec_driver_sql(_THE_COMMIT_IT_IS_FOR[which], (_ANOTHER_COMMIT,))

    assert held_by(store, which).toolchain is None


def a_walk_over(store: PostgresDeckStore, builder: FakeBuildRunner) -> DeckBuilds:
    return DeckBuilds(
        store=store,
        builder=builder,
        clock=FrozenClock(instant=BUILT_AT),
        toolchain_is_present=lambda _: False,
    )


def the_build_columns_written(store: PostgresDeckStore) -> list[str]:
    """What each write to a build or attempt column of the table left in a journal."""
    with store.engine.begin() as connection:
        return [
            slug for (slug,) in connection.exec_driver_sql("SELECT slug FROM written")
        ]


def journal_writes_to_the_build_columns(store: PostgresDeckStore) -> None:
    with store.engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE written (slug TEXT);
            CREATE FUNCTION journal() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN INSERT INTO written VALUES (NEW.slug); RETURN NEW; END $$;
            CREATE TRIGGER journal AFTER UPDATE OF
                active_build, pdf_export, built_commit, built_at, built_toolchain,
                attempt_commit, attempt_started_at, attempt_outcome,
                attempt_failure, attempt_reason, attempt_toolchain
            ON decks FOR EACH ROW EXECUTE FUNCTION journal();
            """
        )


def test_a_walk_that_finds_commit_and_toolchain_unchanged_writes_nothing_to_the_row(
    engine: Engine,
) -> None:
    store = a_store(engine)
    store.put(a_deck())
    builder = FakeBuildRunner(identity=_A_TOOLCHAIN)
    walk = a_walk_over(store, builder)
    walk.build_what_changed(
        a_source("https://example.invalid/decks.git"), building=True
    )
    journal_writes_to_the_build_columns(store)

    again = walk.build_what_changed(
        a_source("https://example.invalid/decks.git"), building=True
    )

    assert (again.delivered, builder.built) == ((), [_SLUG])
    assert the_build_columns_written(store) == []


def test_a_row_an_older_release_moved_to_its_own_commit_is_built_once_more(
    engine: Engine,
) -> None:
    store = a_store(engine)
    store.put(a_deck())
    builder = FakeBuildRunner(identity=_A_TOOLCHAIN)
    walk = a_walk_over(store, builder)
    source = a_source("https://example.invalid/decks.git")
    walk.build_what_changed(source, building=True)
    # The older release builds the folder's next commit and knows no toolchain.
    store.put(a_deck(commit=_ANOTHER_COMMIT))
    with store.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE decks SET built_commit = %s", (_ANOTHER_COMMIT,)
        )

    again = walk.build_what_changed(source, building=True)

    assert (again.delivered, builder.built) == ((True,), [_SLUG, _SLUG])
