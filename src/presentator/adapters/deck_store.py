"""The deck table: a deck's row, its build and its last attempt."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from presentator.adapters.sqlite import rows
from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    Deck,
    ToolchainIdentity,
)

_ONE_ROW: Final = 1

# Nothing a build wrote is touched by this statement, on purpose: taking a deck
# in again must not unpresent the talk that already stands, nor take away the
# PDF that is already downloadable (line 16). Whether the row is marked removed
# is left out too: that mark is `mark_removed_except`'s alone, cleared there
# when the folder is carried again. The row is written only where it already
# belongs to the source writing it, so a slug is never taken from its source.
_PUT_DECK: Final = """
INSERT INTO decks (slug, title, changed_at, owner_id, source_id, commit_sha)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(slug) DO UPDATE SET
    title = excluded.title,
    changed_at = excluded.changed_at,
    owner_id = excluded.owner_id,
    commit_sha = excluded.commit_sha
WHERE decks.source_id = excluded.source_id
"""
# One statement, so everything a build wrote moves at once: no reader can find
# the talk of one commit beside the PDF or the build time of another. The
# attempt goes in the same write, because a talk that stands has nothing left
# to report about how it came about.
_PUT_BUILD: Final = """
UPDATE decks
SET active_build = ?, pdf_export = ?, built_commit = ?, built_at = ?,
    built_toolchain = ?, attempt_toolchain = NULL,
    attempt_commit = NULL, attempt_started_at = NULL,
    attempt_outcome = NULL, attempt_failure = NULL, attempt_reason = NULL,
    attempt_written_megabytes = NULL, attempt_allowed_megabytes = NULL,
    attempt_allowed_entries = NULL
WHERE slug = ?
"""
# Nothing the build wrote is touched here: a run that begins, and a run that
# failed, leave the talk that stands and its PDF where they are (line 16).
_PUT_ATTEMPT: Final = """
UPDATE decks
SET attempt_commit = ?, attempt_started_at = ?,
    attempt_outcome = ?, attempt_failure = ?, attempt_reason = ?,
    attempt_written_megabytes = ?, attempt_allowed_megabytes = ?,
    attempt_allowed_entries = ?, attempt_toolchain = ?
WHERE slug = ?
"""
# The select every adapter reads a deck row by, in the order `DeckRow` holds.
DECK_SELECT: Final = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha,
       active_build, pdf_export, built_commit, built_at,
       attempt_commit, attempt_started_at, attempt_outcome, attempt_failure,
       attempt_reason,
       attempt_written_megabytes, attempt_allowed_megabytes,
       attempt_allowed_entries, built_toolchain, attempt_toolchain
FROM decks
"""
_ALL_DECKS: Final = f"{DECK_SELECT} WHERE removed_at IS NULL"
_ONE_DECK: Final = f"{_ALL_DECKS} AND slug = ?"
# Every row this source ever carried, marked removed or not: a source going
# away takes the decks it stopped carrying with it too, not only the ones
# still listed.
_DECKS_OF_SOURCE: Final = f"{DECK_SELECT} WHERE source_id = ?"
_REMOVE_DECKS_OF_SOURCE: Final = "DELETE FROM decks WHERE source_id = ?"
_MARK_REMOVED: Final = """
UPDATE decks SET removed_at = ?
WHERE removed_at IS NULL AND source_id = ?
  AND slug NOT IN (SELECT value FROM json_each(?))
"""
# ISO text compares as the instants it spells, because every writer stamps
# `removed_at` from the same UTC clock.
_REMOVE_MARKED_BEFORE: Final = """
DELETE FROM decks
WHERE source_id = ? AND removed_at <= ?
"""
_UNMARK_CARRIED: Final = """
UPDATE decks SET removed_at = NULL
WHERE removed_at IS NOT NULL AND source_id = ?
  AND slug IN (SELECT value FROM json_each(?))
"""
# The deck row as an adapter hands it back: what the source carries, then what
# the build wrote, then the attempt beside it. `Instant` is how that adapter
# stores a point in time.
type DeckRow[Instant] = tuple[
    str,
    str,
    Instant,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    Instant | None,
    str | None,
    Instant | None,
    str | None,
    str | None,
    str | None,
    int | None,
    int | None,
    int | None,
    str | None,
    str | None,
]
# What a toolchain is stored as: the commit it was written for, then its digest.
# The digest alone could not say which commit it vouches for, and an older
# release that moves the built commit would leave it standing for the wrong one.
_TOOLCHAIN_SEPARATOR: Final = ":"


@dataclass(frozen=True, slots=True, kw_only=True)
class SqliteDeckStore:
    """The deck table."""

    database: Path

    def put(self, deck: Deck) -> bool:
        """Write the deck under its slug, so a re-pushed folder stays one deck.

        A slug another source already carries is left alone, and the answer
        says so: nothing was written.
        """
        with rows(self.database) as cursor:
            written = cursor.execute(
                _PUT_DECK,
                (
                    deck.slug,
                    deck.title,
                    deck.changed_at.isoformat(),
                    deck.owner_id,
                    deck.source_id,
                    deck.commit,
                ),
            )
            return written.rowcount == _ONE_ROW

    def put_build(self, slug: str, build: Build) -> None:
        """Switch everything the deck says about its talk over, in one write."""
        with rows(self.database) as cursor:
            cursor.execute(
                _PUT_BUILD,
                (
                    str(build.directory),
                    str(build.pdf),
                    build.commit,
                    build.built_at.isoformat(),
                    toolchain_column(build.commit, build.toolchain),
                    slug,
                ),
            )

    def put_attempt(self, slug: str, attempt: BuildAttempt | None) -> None:
        """Write down the build this deck last started, and how far it got."""
        with rows(self.database) as cursor:
            columns = attempt_columns(attempt)
            cursor.execute(
                _PUT_ATTEMPT,
                (
                    columns.commit,
                    None
                    if columns.started_at is None
                    else columns.started_at.isoformat(),
                    columns.outcome,
                    columns.failure_text,
                    columns.reason,
                    columns.written_megabytes,
                    columns.allowed_megabytes,
                    columns.allowed_entries,
                    columns.toolchain,
                    slug,
                ),
            )

    def get(self, slug: str) -> Deck | None:
        """Read the one deck row that slug names, while its folder is still there."""
        with rows(self.database) as cursor:
            found = cursor.execute(_ONE_DECK, (slug,)).fetchone()
        return None if found is None else _deck(found)

    def mark_removed_except(
        self,
        present: frozenset[str],
        *,
        source_id: str,
        at: datetime,
    ) -> None:
        """Mark that source's decks outside `present` removed, clear it inside."""
        # A set is no bind parameter, so the folders the source carries travel
        # as one JSON value rather than as SQL built per call.
        carried = json.dumps(sorted(present))
        with rows(self.database) as cursor:
            cursor.execute(_MARK_REMOVED, (at.isoformat(), source_id, carried))
            cursor.execute(_UNMARK_CARRIED, (source_id, carried))

    def remove_marked_before(self, source_id: str, *, before: datetime) -> int:
        """Delete that source's decks marked removed at or before `before`."""
        with rows(self.database) as cursor:
            removed = cursor.execute(
                _REMOVE_MARKED_BEFORE, (source_id, before.isoformat())
            )
            return removed.rowcount

    def all(self) -> tuple[Deck, ...]:
        """Read every deck row whose folder is still at its source."""
        with rows(self.database) as cursor:
            found = cursor.execute(_ALL_DECKS).fetchall()
        return tuple(_deck(row) for row in found)

    def for_source(self, source_id: str) -> tuple[Deck, ...]:
        """Read every deck row this source has ever carried, marked removed or not."""
        with rows(self.database) as cursor:
            found = cursor.execute(_DECKS_OF_SOURCE, (source_id,)).fetchall()
        return tuple(_deck(row) for row in found)

    def remove_for_source(self, source_id: str) -> None:
        """Delete every deck row this source has ever carried, marked removed or not."""
        with rows(self.database) as cursor:
            cursor.execute(_REMOVE_DECKS_OF_SOURCE, (source_id,))


def _deck(row: DeckRow[str]) -> Deck:
    return stored_deck(row, read_instant=datetime.fromisoformat)


def stored_deck[Instant](
    row: DeckRow[Instant], *, read_instant: Callable[[Instant], datetime]
) -> Deck:
    """The deck of one row, its instants read the way that adapter stores them."""
    slug, title, changed_at, owner_id, source_id, commit = row[:6]
    return Deck(
        slug=slug,
        title=title,
        changed_at=read_instant(changed_at),
        owner_id=owner_id,
        source_id=source_id,
        commit=commit,
        build=_build(row[6:10], stored_toolchain=row[18], read_instant=read_instant),
        attempt=_attempt(
            row[10:18], stored_toolchain=row[19], read_instant=read_instant
        ),
    )


def _build[Instant](
    columns: tuple[str | None, str | None, str | None, Instant | None],
    *,
    stored_toolchain: str | None,
    read_instant: Callable[[Instant], datetime],
) -> Build | None:
    """What the row says its talk is, or nothing while no build has switched."""
    directory, pdf, commit, built_at = columns
    if directory is None or pdf is None or commit is None or built_at is None:
        return None
    return Build(
        directory=Path(directory),
        pdf=Path(pdf),
        commit=commit,
        built_at=read_instant(built_at),
        toolchain=_read_toolchain(stored_toolchain, commit),
    )


def toolchain_column(commit: str, toolchain: ToolchainIdentity | None) -> str | None:
    """The one column value a toolchain is written as, with the commit it is for."""
    if toolchain is None:
        return None
    return f"{commit}{_TOOLCHAIN_SEPARATOR}{toolchain.digest}"


def _read_toolchain(stored: str | None, commit: str) -> ToolchainIdentity | None:
    """The toolchain stored for exactly this commit, or nothing where it is unknown.

    A row from before, a value that is no identity, and one written for another
    commit all read as unknown, so the deck builds once more.
    """
    if stored is None:
        return None
    written_for, separator, digest = stored.rpartition(_TOOLCHAIN_SEPARATOR)
    if not separator or written_for != commit:
        return None
    try:
        return ToolchainIdentity(digest)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class AttemptColumns:
    """The columns an attempt is written as; no attempt is all of them empty."""

    commit: str | None = None
    started_at: datetime | None = None
    outcome: str | None = None
    failure_text: str | None = None
    reason: str | None = None
    written_megabytes: int | None = None
    allowed_megabytes: int | None = None
    allowed_entries: int | None = None
    toolchain: str | None = None


def attempt_columns(attempt: BuildAttempt | None) -> AttemptColumns:
    """The columns that write this attempt, or put the row back to carrying none."""
    if attempt is None:
        return AttemptColumns()
    failure = attempt.failure
    return AttemptColumns(
        commit=attempt.commit,
        started_at=attempt.started_at,
        outcome=attempt.outcome.value,
        failure_text=None if failure is None else failure.text,
        reason=None if failure is None else failure.reason.value,
        written_megabytes=None if failure is None else failure.written_megabytes,
        allowed_megabytes=None if failure is None else failure.allowed_megabytes,
        allowed_entries=None if failure is None else failure.allowed_entries,
        toolchain=toolchain_column(attempt.commit, attempt.toolchain),
    )


def _attempt[Instant](
    columns: tuple[
        str | None,
        Instant | None,
        str | None,
        str | None,
        str | None,
        int | None,
        int | None,
        int | None,
    ],
    *,
    stored_toolchain: str | None,
    read_instant: Callable[[Instant], datetime],
) -> BuildAttempt | None:
    """The build the row says ran last, or nothing while none is worth telling."""
    commit, started_at, outcome, text, reason = columns[:5]
    written_megabytes, allowed_megabytes, allowed_entries = columns[5:]
    if commit is None or started_at is None or outcome is None:
        return None
    if reason is None:
        # A failed attempt always carries a reason; no writer leaves a row
        # without one.
        failure = None
    else:
        failure = BuildFailure(
            reason=BuildFailureReason(reason),
            text=text,
            written_megabytes=written_megabytes,
            allowed_megabytes=allowed_megabytes,
            allowed_entries=allowed_entries,
        )
    attempt = BuildAttempt(
        commit=commit,
        started_at=read_instant(started_at),
        outcome=BuildOutcome(outcome),
        failure=failure,
        toolchain=_read_toolchain(stored_toolchain, commit),
    )
    if attempt.outcome is BuildOutcome.FAILED and failure is None:
        message = f"the failed attempt of commit {commit} carries no reason"
        raise ValueError(message)
    return attempt
