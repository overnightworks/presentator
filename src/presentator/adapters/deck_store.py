"""The deck table (ADR 0006): a deck's row, its build and its last attempt."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

from presentator.adapters.sqlite import rows
from presentator.contracts.decks import Build, BuildAttempt, BuildOutcome, Deck

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
    attempt_commit = NULL, attempt_started_at = NULL,
    attempt_outcome = NULL, attempt_failure = NULL
WHERE slug = ?
"""
# Nothing the build wrote is touched here: a run that begins, and a run that
# failed, leave the talk that stands and its PDF where they are (line 16).
_PUT_ATTEMPT: Final = """
UPDATE decks
SET attempt_commit = ?, attempt_started_at = ?,
    attempt_outcome = ?, attempt_failure = ?
WHERE slug = ?
"""
_ALL_DECKS: Final = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha,
       active_build, pdf_export, built_commit, built_at,
       attempt_commit, attempt_started_at, attempt_outcome, attempt_failure
FROM decks
WHERE removed_at IS NULL
"""
_ONE_DECK: Final = f"{_ALL_DECKS} AND slug = ?"
# Every row this source ever carried, marked removed or not: a source going
# away takes the decks it stopped carrying with it too, not only the ones
# still listed.
_DECKS_OF_SOURCE: Final = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha,
       active_build, pdf_export, built_commit, built_at,
       attempt_commit, attempt_started_at, attempt_outcome, attempt_failure
FROM decks
WHERE source_id = ?
"""
_REMOVE_DECKS_OF_SOURCE: Final = "DELETE FROM decks WHERE source_id = ?"
_MARK_REMOVED: Final = """
UPDATE decks SET removed_at = ?
WHERE removed_at IS NULL AND source_id = ?
  AND slug NOT IN (SELECT value FROM json_each(?))
"""
_UNMARK_CARRIED: Final = """
UPDATE decks SET removed_at = NULL
WHERE removed_at IS NOT NULL AND source_id = ?
  AND slug IN (SELECT value FROM json_each(?))
"""
# The deck row as SQLite hands it back: what the source carries, then what the
# build wrote, then the attempt beside it — each group four values or none.
_DeckRow = tuple[
    str,
    str,
    str,
    str,
    str,
    str,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
]


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
                    slug,
                ),
            )

    def put_attempt(self, slug: str, attempt: BuildAttempt) -> None:
        """Write down the build this deck last started, and how far it got."""
        with rows(self.database) as cursor:
            cursor.execute(
                _PUT_ATTEMPT,
                (
                    attempt.commit,
                    attempt.started_at.isoformat(),
                    attempt.outcome.value,
                    attempt.failure,
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


def _deck(row: _DeckRow) -> Deck:
    slug, title, changed_at, owner_id, source_id, commit = row[:6]
    return Deck(
        slug=slug,
        title=title,
        changed_at=datetime.fromisoformat(changed_at),
        owner_id=owner_id,
        source_id=source_id,
        commit=commit,
        build=_build(*row[6:10]),
        attempt=_attempt(*row[10:]),
    )


def _build(
    directory: str | None,
    pdf: str | None,
    commit: str | None,
    built_at: str | None,
) -> Build | None:
    """What the row says its talk is, or nothing while no build has switched."""
    if directory is None or pdf is None or commit is None or built_at is None:
        return None
    return Build(
        directory=Path(directory),
        pdf=Path(pdf),
        commit=commit,
        built_at=datetime.fromisoformat(built_at),
    )


def _attempt(
    commit: str | None,
    started_at: str | None,
    outcome: str | None,
    failure: str | None,
) -> BuildAttempt | None:
    """The build the row says ran last, or nothing while none is worth telling."""
    if commit is None or started_at is None or outcome is None:
        return None
    return BuildAttempt(
        commit=commit,
        started_at=datetime.fromisoformat(started_at),
        outcome=BuildOutcome(outcome),
        failure=failure,
    )
