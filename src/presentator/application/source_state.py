"""The word a poll, a check or a deck gets, a run as shown, a source as listed."""

from typing import Final

from presentator.contracts.decks import (
    BuildOutcome,
    ConnectionCheckResult,
    Deck,
    DeckState,
    ListedSource,
    ShownSourceRun,
    Source,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
    access_kind_of,
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


def listed_source(source: Source, newest: SourceRun | None) -> ListedSource:
    """The row for that source, derived from its newest run and nothing else."""
    return ListedSource(
        name=source.name,
        url=source.url,
        access=access_kind_of(source.url),
        state=SourceState.NEVER_FETCHED if newest is None else source_state_of(newest),
        reason=None if newest is None else newest.reason,
        fetched_at=None if newest is None else newest.at,
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
    opens.
    """
    attempt = deck.attempt
    if attempt is None:
        return DeckState.READY if deck.build is not None else DeckState.NEVER_BUILT
    if attempt.outcome is BuildOutcome.RUNNING:
        return DeckState.BUILDING
    return DeckState.FAILED


def _failed_state_of(reason: SourceRunFailure | None) -> SourceState:
    """A refusal and failed keep their own word; any other failure is error."""
    if reason is not None and reason.is_refusal:
        return SourceState.REFUSED
    if reason is SourceRunFailure.FAILED:
        return SourceState.FAILED
    return SourceState.ERROR
