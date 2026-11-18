"""Which deck rows and runs of the build stay, and which go.

Rows decide and the store obeys: a run any deck row names, marked removed or
not, stays; every other run in the store or on this disk is taken out of both.
A listing that did not finish refuses the whole decision, because every run
would then look unreferenced. A row marked removed goes once its folder has
been missing for the period the privacy section states, and that pass's sweep
takes its runs.

The store is a cache the rows rebuild, as `mirrors/` is: a run a row names that
the store no longer holds is built again by the next refresh, so a restore of
the database alone is enough.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from operator import attrgetter
from pathlib import PurePosixPath
from typing import Final

from presentator.contracts.decks import Build, BuildRun, Deck
from presentator.ports.decks import BuildRunner, DeckStore, SourceStore

_RETENTION_REFUSED: Final = "build_retention.refused"
_SWEEP_COMPLETED: Final = "build_sweep.completed"
_DECK_RETENTION_COMPLETED: Final = "deck_retention.completed"
_NOT_LISTED: Final = "runs_not_listed"
# How long a deck stays after a pass first missed its folder; the privacy
# section states the same number.
REMOVED_DECK_KEPT: Final = timedelta(days=30)

_log = logging.getLogger(__name__)


def sweep(builder: BuildRunner, sources: SourceStore, store: DeckStore) -> None:
    """Take every run no deck row names out of the store and off this disk.

    A failed removal is tried again next pass.

    A sweep that removed or left a run is one `build_sweep.completed` line with the
    counts; one that found nothing to take is silent.

    Every row of every source counts, because a deck row is only written under a
    source and a source is only removed after its rows.
    """
    named = _runs_named_by(
        [deck for source in sources.all() for deck in store.for_source(source.id)],
    )
    kept = builder.runs()
    if kept is None:
        _log.info(_RETENTION_REFUSED, extra={"reason": _NOT_LISTED})
        return
    attempts = [builder.remove(run) for run in kept - named]
    if attempts:
        stayed = sum(1 for left in attempts if left is not None)
        _log.info(
            _SWEEP_COMPLETED,
            extra={
                "runs_removed_count": len(attempts) - stayed,
                "runs_stayed_count": stayed,
            },
        )


def standing_build(builder: BuildRunner, deck: Deck) -> Build | None:
    """The build that row delivers, unless the store lost its run.

    A listing that did not finish proves nothing is missing, as the sweep
    refuses to act on it.
    """
    build = deck.build
    if build is None:
        return None
    kept = builder.runs()
    return None if kept is not None and build.run not in kept else build


def delete_absent(
    store: DeckStore, *, present: frozenset[str], source_id: str, at: datetime
) -> None:
    """Mark that source's decks outside `present`, then delete those marked too long.

    Only a pass that read the source calls this, and the marks of the folders
    it saw are cleared before anything is deleted, so a folder that came back
    keeps its deck however late the pass that sees it runs.
    """
    store.mark_removed_except(present, source_id=source_id, at=at)
    deleted = store.remove_marked_before(source_id, before=at - REMOVED_DECK_KEPT)
    if deleted:
        _log.info(
            _DECK_RETENTION_COMPLETED,
            extra={"source_id": source_id, "decks_deleted_count": deleted},
        )


def builds_removed(
    builder: BuildRunner, decks: Iterable[Deck]
) -> tuple[PurePosixPath, ...]:
    """Take every run these decks deliver away, and name the ones that stayed."""
    runs = sorted(_runs_named_by(decks), key=attrgetter("name"))
    attempts = (builder.remove(run) for run in runs)
    return tuple(stayed for stayed in attempts if stayed is not None)


def _runs_named_by(decks: Iterable[Deck]) -> frozenset[BuildRun]:
    """The runs these decks deliver from."""
    return frozenset(deck.build.run for deck in decks if deck.build is not None)
