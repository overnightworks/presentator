"""The decks table on Postgres, behind `DeckStore`."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, cast

from sqlalchemy import Engine, Row, Text, TextClause, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from presentator.adapters.deck_store import (
    DECK_SELECT,
    DeckRow,
    attempt_columns,
    stored_deck,
    toolchain_column,
)
from presentator.adapters.postgres.engine import in_utc
from presentator.adapters.postgres.locks import (
    LockClass,
    hold_until_transaction_ends,
    lock_of,
)
from presentator.contracts.decks import Build, BuildAttempt, Deck

# A slug already written by another source stays that source's: the conflict
# update applies only where the row belongs to the source writing it, and the
# statement then touches no row. Nothing a build wrote and not the removal mark
# is part of it.
_PUT_DECK: Final = text(
    "INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha)"
    " VALUES (:slug, :title, :changed_at, :owner_id, :source_id, :commit_sha)"
    " ON CONFLICT (slug) DO UPDATE SET"
    " title = EXCLUDED.title, changed_at = EXCLUDED.changed_at,"
    " owner_id = EXCLUDED.owner_id, commit_sha = EXCLUDED.commit_sha"
    " WHERE decks.source_id = EXCLUDED.source_id"
)
# One statement, so everything a build wrote moves at once and no reader sees
# the talk of one commit beside the PDF or build time of another.
_PUT_BUILD: Final = text(
    "UPDATE decks SET active_build = :directory, pdf_export = :pdf,"
    " built_commit = :commit_sha, built_at = :built_at,"
    " built_toolchain = :toolchain, attempt_toolchain = NULL,"
    " attempt_commit = NULL, attempt_started_at = NULL,"
    " attempt_outcome = NULL, attempt_failure = NULL, attempt_reason = NULL,"
    " attempt_written_megabytes = NULL, attempt_allowed_megabytes = NULL,"
    " attempt_allowed_entries = NULL"
    " WHERE slug = :slug"
)
_PUT_ATTEMPT: Final = text(
    "UPDATE decks SET attempt_commit = :commit, attempt_started_at = :started_at,"
    " attempt_outcome = :outcome, attempt_failure = :failure_text,"
    " attempt_reason = :reason, attempt_written_megabytes = :written_megabytes,"
    " attempt_allowed_megabytes = :allowed_megabytes,"
    " attempt_allowed_entries = :allowed_entries, attempt_toolchain = :toolchain"
    " WHERE slug = :slug"
)
_ALL_DECKS: Final = text(f"{DECK_SELECT} WHERE removed_at IS NULL")
_ONE_DECK: Final = text(f"{DECK_SELECT} WHERE removed_at IS NULL AND slug = :slug")
_DECKS_OF_SOURCE: Final = text(f"{DECK_SELECT} WHERE source_id = :source_id")
_REMOVE_DECKS_OF_SOURCE: Final = text("DELETE FROM decks WHERE source_id = :source_id")
_MARK_REMOVED: Final = text(
    "UPDATE decks SET removed_at = :at"
    " WHERE removed_at IS NULL AND source_id = :source_id"
    " AND slug <> ALL(:present)"
).bindparams(bindparam("present", type_=ARRAY(Text())))
_UNMARK_CARRIED: Final = text(
    "UPDATE decks SET removed_at = NULL"
    " WHERE removed_at IS NOT NULL AND source_id = :source_id"
    " AND slug = ANY(:present)"
).bindparams(bindparam("present", type_=ARRAY(Text())))
_REMOVE_MARKED_BEFORE: Final = text(
    "DELETE FROM decks WHERE source_id = :source_id AND removed_at <= :before"
)
_ONE_ROW: Final = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class PostgresDeckStore:
    """The `decks` table: a deck's row, its build and its last attempt.

    The slug is the primary key, so two processes writing one slug from two
    sources get one winner and one refusal. Marking a source's decks removed
    reads and writes the same rows twice, so it takes the source's own advisory
    lock first: two processes marking one source leave what one of them would
    alone, where interleaved statements could leave a mixture of the two.
    """

    engine: Engine

    def put(self, deck: Deck) -> bool:
        """Write the deck under its slug unless another source already carries it."""
        with self.engine.begin() as connection:
            written = connection.execute(
                _PUT_DECK,
                {
                    "slug": deck.slug,
                    "title": deck.title,
                    "changed_at": deck.changed_at,
                    "owner_id": deck.owner_id,
                    "source_id": deck.source_id,
                    "commit_sha": deck.commit,
                },
            )
        return written.rowcount == _ONE_ROW

    def put_build(self, slug: str, build: Build) -> None:
        """Switch everything the deck says about its talk over, in one write."""
        with self.engine.begin() as connection:
            connection.execute(
                _PUT_BUILD,
                {
                    "directory": str(build.directory),
                    "pdf": str(build.pdf),
                    "commit_sha": build.commit,
                    "built_at": build.built_at,
                    "toolchain": toolchain_column(build.commit, build.toolchain),
                    "slug": slug,
                },
            )

    def put_attempt(self, slug: str, attempt: BuildAttempt | None) -> None:
        """Write down the build this deck last started, and how far it got."""
        columns = attempt_columns(attempt)
        with self.engine.begin() as connection:
            connection.execute(
                _PUT_ATTEMPT,
                {
                    "commit": columns.commit,
                    "started_at": columns.started_at,
                    "outcome": columns.outcome,
                    "failure_text": columns.failure_text,
                    "reason": columns.reason,
                    "written_megabytes": columns.written_megabytes,
                    "allowed_megabytes": columns.allowed_megabytes,
                    "allowed_entries": columns.allowed_entries,
                    "toolchain": columns.toolchain,
                    "slug": slug,
                },
            )

    def get(self, slug: str) -> Deck | None:
        """Read the one deck row that slug names, while its folder is still there."""
        with self.engine.connect() as connection:
            found = connection.execute(_ONE_DECK, {"slug": slug}).fetchone()
        return None if found is None else _deck(found)

    def mark_removed_except(
        self,
        present: frozenset[str],
        *,
        source_id: str,
        at: datetime,
    ) -> None:
        """Mark that source's decks outside `present` removed, clear it inside."""
        carried = sorted(present)
        with self.engine.begin() as connection:
            hold_until_transaction_ends(
                connection, lock_of(LockClass.DECK_MARKS, source_id)
            )
            connection.execute(
                _MARK_REMOVED,
                {"at": at, "source_id": source_id, "present": carried},
            )
            connection.execute(
                _UNMARK_CARRIED, {"source_id": source_id, "present": carried}
            )

    def remove_marked_before(self, source_id: str, *, before: datetime) -> int:
        """Delete that source's decks marked removed at or before `before`."""
        with self.engine.begin() as connection:
            removed = connection.execute(
                _REMOVE_MARKED_BEFORE, {"source_id": source_id, "before": before}
            )
        return removed.rowcount

    def all(self) -> tuple[Deck, ...]:
        """Read every deck row whose folder is still at its source."""
        return self._read(_ALL_DECKS, {})

    def for_source(self, source_id: str) -> tuple[Deck, ...]:
        """Read every deck row this source has ever carried, marked removed or not."""
        return self._read(_DECKS_OF_SOURCE, {"source_id": source_id})

    def remove_for_source(self, source_id: str) -> None:
        """Delete every deck row this source has ever carried, marked removed or not."""
        with self.engine.begin() as connection:
            connection.execute(_REMOVE_DECKS_OF_SOURCE, {"source_id": source_id})

    def _read(
        self, statement: TextClause, parameters: Mapping[str, str]
    ) -> tuple[Deck, ...]:
        with self.engine.connect() as connection:
            found = connection.execute(statement, parameters).all()
        return tuple(_deck(row) for row in found)


def _deck(row: Row[Any]) -> Deck:
    return stored_deck(cast("DeckRow[datetime]", tuple(row)), read_instant=in_utc)
