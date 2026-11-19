"""A source run row worded by a newer release reads back as a failed poll."""

import pytest
from sqlalchemy import Engine

from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.contracts.decks import SourceRun, SourceRunFailure, SourceRunOutcome
from tests.support.deck_source_support import PUSHED_AT, SOURCE_ID, seed_source_rows
from tests.support.postgres import NO_FENCE


def a_failed_run(reason: SourceRunFailure) -> SourceRun:
    return SourceRun(
        source_id=SOURCE_ID,
        at=PUSHED_AT,
        outcome=SourceRunOutcome.FAILURE,
        commit=None,
        reason=reason,
    )


@pytest.mark.parametrize(
    "newer_word",
    [
        pytest.param(
            "UPDATE source_runs SET reason = 'from-a-newer-release'", id="reason"
        ),
        pytest.param(
            "UPDATE source_runs SET outcome = 'from-a-newer-release'", id="outcome"
        ),
    ],
)
def test_a_run_worded_by_a_newer_release_reads_as_a_failed_poll(
    engine: Engine, newer_word: str
) -> None:
    """A rollback keeps listing a source whose run a newer release wrote."""
    seed_source_rows(engine, SOURCE_ID)
    store = PostgresSourceRunStore(engine=engine, fence=NO_FENCE)
    store.record(a_failed_run(SourceRunFailure.UNREACHABLE))
    with engine.begin() as connection:
        connection.exec_driver_sql(newer_word)

    read = store.newest(SOURCE_ID)

    assert read == a_failed_run(SourceRunFailure.FAILED)
