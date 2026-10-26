"""Which runs of the build the deck rows still deliver, and which go.

Rows decide and files obey: a run any deck row names through its talk or its
PDF, marked removed or not, stays; every other run on disk is taken off it. A
stored path the build runner cannot place, or a listing that did not finish,
refuses the whole decision, because every run would then look unreferenced.
"""

import logging
from collections.abc import Iterable
from typing import Final

from presentator.contracts.decks import Deck
from presentator.ports.decks import BuildRun, BuildRunner, DeckStore, SourceStore

_UNPLACED: Final = "build %s is not a run under the builds root"
_NOT_LISTED: Final = "the runs on disk could not be listed, nothing is swept"

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
