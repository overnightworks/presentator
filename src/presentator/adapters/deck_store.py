"""The deck row: how its build, its last attempt and its toolchain read and write."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from presentator.adapters.persisted import read_persisted
from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    BuildRun,
    Deck,
    ToolchainIdentity,
    UnplacedBuildError,
)

# The select every adapter reads a deck row by, in the order `DeckRow` holds.
DECK_SELECT: Final = """
SELECT slug, title, changed_at, owner_id, source_id, commit_sha,
       built_run, built_commit, built_at, built_toolchain,
       attempt_commit, attempt_started_at, attempt_outcome, attempt_failure,
       attempt_reason,
       attempt_written_megabytes, attempt_allowed_megabytes,
       attempt_allowed_entries, attempt_toolchain
FROM decks
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
    Instant | None,
    str | None,
    str | None,
    Instant | None,
    str | None,
    str | None,
    str | None,
    int | None,
    int | None,
    int | None,
    str | None,
]
# What a toolchain is stored as: the commit it was written for, then its digest.
# The digest alone could not say which commit it vouches for, and an older
# release that moves the built commit would leave it standing for the wrong one.
_TOOLCHAIN_SEPARATOR: Final = ":"


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
        build=_build(row[6:10], source_id=source_id, read_instant=read_instant),
        attempt=_attempt(
            row[10:18], stored_toolchain=row[18], read_instant=read_instant
        ),
    )


def _build[Instant](
    columns: tuple[str | None, str | None, Instant | None, str | None],
    *,
    source_id: str,
    read_instant: Callable[[Instant], datetime],
) -> Build | None:
    """What the row says its talk is, or nothing while no build has switched.

    A build written before builds were kept in the store names no run, so it
    is no talk any process can serve, and the next refresh builds it again.
    A run that is no single name is a corrupted row and fails loud.
    """
    run, commit, built_at, stored_toolchain = columns
    if run is None or commit is None or built_at is None:
        return None
    try:
        stored_run = BuildRun(run)
    except ValueError as refused:
        raise UnplacedBuildError(source_id) from refused
    return Build(
        run=stored_run,
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
    known_outcome = read_persisted(BuildOutcome, outcome, unknown=None)
    known_reason = (
        None
        if reason is None
        else read_persisted(BuildFailureReason, reason, unknown=None)
    )
    if known_outcome is None or (reason is not None and known_reason is None):
        # A newer release worded it: it is listed as a failure that says
        # nothing, which counts as tried rather than being built over and over.
        known_outcome, failure = BuildOutcome.FAILED, BuildFailure.without_words()
    elif known_reason is None:
        # A failed attempt always carries a reason; no writer leaves a row
        # without one.
        failure = None
    else:
        failure = BuildFailure(
            reason=known_reason,
            text=text,
            written_megabytes=written_megabytes,
            allowed_megabytes=allowed_megabytes,
            allowed_entries=allowed_entries,
        )
    attempt = BuildAttempt(
        commit=commit,
        started_at=read_instant(started_at),
        outcome=known_outcome,
        failure=failure,
        toolchain=_read_toolchain(stored_toolchain, commit),
    )
    if attempt.outcome is BuildOutcome.FAILED and failure is None:
        message = f"the failed attempt of commit {commit} carries no reason"
        raise ValueError(message)
    return attempt
