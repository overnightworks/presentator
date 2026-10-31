"""Which deck rows and runs of the build stay, and which go.

Rows decide and files obey: a run any deck row names through its talk or its
PDF, marked removed or not, stays; every other run on disk is taken off it. A
stored path the build runner cannot place, or a listing that did not finish,
refuses the whole decision, because every run would then look unreferenced. A
row marked removed goes once its folder has been missing for the period the
privacy section states, and that pass's sweep takes its runs.
"""

import logging
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Final

from presentator.contracts.decks import Deck
from presentator.ports.decks import BuildRun, BuildRunner, DeckStore, SourceStore

_UNPLACED: Final = "build %s is not a run under the builds root"
_NOT_LISTED: Final = "the runs on disk could not be listed, nothing is swept"
_DELETED_ABSENT: Final = "deleted %d decks of source %s missing past their retention"
# How long a deck stays after a pass first missed its folder; the privacy
# section states the same number.
REMOVED_DECK_KEPT: Final = timedelta(days=30)

_log = logging.getLogger(__name__)


def sweep(builder: BuildRunner, sources: SourceStore, store: DeckStore) -> None:
    """Take every run no deck row names off disk; a failed removal is tried next pass.

    Every row of every source counts, because a deck row is only written under a
    source and a source is only removed after its rows.
    """
    named = _runs_named_by(
        builder,
        [deck for source in sources.all() for deck in store.for_source(source.id)],
    )
    on_disk = builder.runs()
    if on_disk is None:
        _log.warning(_NOT_LISTED)
    if named is None or on_disk is None:
        return
    for run in on_disk - named:
        builder.remove(run)


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
        _log.info(_DELETED_ABSENT, deleted, source_id)


def builds_removed(builder: BuildRunner, decks: Iterable[Deck]) -> bool:
    """Whether every run these decks deliver from is gone from disk."""
    runs = _runs_named_by(builder, decks)
    if runs is None:
        return False
    stayed = [run for run in runs if not builder.remove(run)]
    return not stayed


def _runs_named_by(
    builder: BuildRunner, decks: Iterable[Deck]
) -> frozenset[BuildRun] | None:
    """The runs these decks deliver from, or nothing where one path is not placed."""
    runs: set[BuildRun] = set()
    for deck in decks:
        if deck.build is None:
            continue
        for path in (deck.build.directory, deck.build.pdf):
            run = builder.run_of(path)
            if run is None:
                _log.warning(_UNPLACED, path)
                return None
            runs.add(run)
    return frozenset(runs)
