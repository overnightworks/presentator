"""The source_runs table on Postgres, behind `SourceRuns`."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Engine, text

from presentator.adapters.decks import stored_source_run
from presentator.adapters.postgres.engine import in_utc
from presentator.adapters.postgres.locks import (
    LockClass,
    hold_until_transaction_ends,
    lock_of,
)
from presentator.adapters.postgres.refresh_owner import RefreshFence
from presentator.contracts.decks import RECENT_SOURCE_RUNS, SourceRun

_RECORD_RUN: Final = text(
    "INSERT INTO source_runs (source_id, at, outcome, commit_sha, reason)"
    " VALUES (:source_id, :at, :outcome, :commit_sha, :reason)"
)
_PRUNE_OLDER_RUNS: Final = text(
    "DELETE FROM source_runs WHERE source_id = :source_id AND id NOT IN ("
    "SELECT id FROM source_runs WHERE source_id = :source_id"
    " ORDER BY id DESC LIMIT :keep_newest)"
)
# `id` orders runs of the same source recorded at one identical instant, which a
# frozen test clock can produce; `at` alone cannot break that tie.
_RECENT_RUNS: Final = text(
    "SELECT source_id, at, outcome, commit_sha, reason FROM source_runs"
    " WHERE source_id = :source_id ORDER BY id DESC LIMIT :count"
)
_REMOVE_RUNS: Final = text("DELETE FROM source_runs WHERE source_id = :source_id")
_NEWEST_ONLY: Final = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresSourceRunStore:
    """The `source_runs` table: the newest polls of each source, bounded.

    Recording is an insert and a prune that must see each other's work, so it
    takes the source's own advisory lock first: two processes recording the same
    source's runs one after another keep exactly the bound, where two prunes
    that cannot see each other's insert would keep one more. Every write is the
    refresh holder's and asks the fence first.
    """

    engine: Engine
    fence: RefreshFence

    def record(self, run: SourceRun) -> None:
        """Add the run and drop older ones of that source past the bound."""
        with self.engine.begin() as connection:
            self.fence.check(connection)
            hold_until_transaction_ends(
                connection, lock_of(LockClass.SOURCE_RUNS, run.source_id)
            )
            connection.execute(
                _RECORD_RUN,
                {
                    "source_id": run.source_id,
                    "at": run.at,
                    "outcome": run.outcome.value,
                    "commit_sha": run.commit,
                    "reason": None if run.reason is None else run.reason.value,
                },
            )
            connection.execute(
                _PRUNE_OLDER_RUNS,
                {"source_id": run.source_id, "keep_newest": RECENT_SOURCE_RUNS},
            )

    def newest(self, source_id: str) -> SourceRun | None:
        """Read that source's newest row, or nothing while it carries none."""
        found = self._newest_first(source_id, _NEWEST_ONLY)
        return found[0] if found else None

    def recent(self, source_id: str) -> tuple[SourceRun, ...]:
        """Read that source's newest rows, newest first, no more than the bound."""
        return self._newest_first(source_id, RECENT_SOURCE_RUNS)

    def remove_for_source(self, source_id: str) -> None:
        """Delete every run this source has ever recorded."""
        with self.engine.begin() as connection:
            self.fence.check(connection)
            connection.execute(_REMOVE_RUNS, {"source_id": source_id})

    def _newest_first(self, source_id: str, count: int) -> tuple[SourceRun, ...]:
        with self.engine.connect() as connection:
            found = connection.execute(
                _RECENT_RUNS, {"source_id": source_id, "count": count}
            ).all()
        return tuple(_run_of(row) for row in found)


def _run_of(row: Sequence[Any]) -> SourceRun:
    source_id, at, outcome, commit, reason = row
    return stored_source_run(source_id, in_utc(at), outcome, commit, reason)
