"""The one word a recorded poll or a connection check gets."""

from presentator.contracts.decks import (
    ConnectionCheckResult,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
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


def _failed_state_of(reason: SourceRunFailure | None) -> SourceState:
    """Refused and failed keep their own word; every other failure folds into error."""
    if reason is SourceRunFailure.REFUSED:
        return SourceState.REFUSED
    if reason is SourceRunFailure.FAILED:
        return SourceState.FAILED
    return SourceState.ERROR
