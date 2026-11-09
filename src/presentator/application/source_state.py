"""The one word a poll, a connection check or a deck gets, and a run as shown."""

from typing import Final

from presentator.contracts.decks import (
    BuildOutcome,
    ConnectionCheckResult,
    Deck,
    DeckState,
    ShownSourceRun,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)

# A person compares the commit on the page with the one their push wrote, and
# reads it off the screen; the first characters are what git itself shows.
SHORT_COMMIT: Final = 7


def shown_run(run: SourceRun) -> ShownSourceRun:
    """That run as a page shows it, its commit short."""
    return ShownSourceRun(
        outcome=run.outcome,
        state=source_state_of(run),
        at=run.at,
        commit=None if run.commit is None else run.commit[:SHORT_COMMIT],
        reason=run.reason,
    )


def source_state_of(run: SourceRun) -> SourceState:
    """The word the sources list says for a recorded run."""
    if run.outcome is SourceRunOutcome.SUCCESS:
        return SourceState.REACHABLE
    return _failed_state_of(run.reason)


def connection_state_of(checked: ConnectionCheckResult) -> SourceState:
    """The word a check gets: the one a run that failed the same way lists as."""
    if checked.failure is None:
        return SourceState.REACHABLE
    return _failed_state_of(checked.failure)


def deck_state_of(deck: Deck) -> DeckState:
    """The one word for what this deck is, read off its talk and its attempt.

    An attempt outranks the talk that stands, because it is the newer news: a
    deck whose last build failed is failed even while yesterday's talk still
    opens (line 16).
    """
    attempt = deck.attempt
    if attempt is None:
        return DeckState.READY if deck.build is not None else DeckState.NEVER_BUILT
    if attempt.outcome is BuildOutcome.RUNNING:
        return DeckState.BUILDING
    return DeckState.FAILED


def _failed_state_of(reason: SourceRunFailure | None) -> SourceState:
    """Refused and failed keep their own word; every other failure folds into error."""
    if reason is SourceRunFailure.REFUSED:
        return SourceState.REFUSED
    if reason is SourceRunFailure.FAILED:
        return SourceState.FAILED
    return SourceState.ERROR
