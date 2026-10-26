"""Which runs of the build a refresh pass leaves on disk: those a deck row names."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from presentator.application.decks import Decks, SourceRemovalRefusal
from presentator.contracts.decks import Build, Deck, Source, SourcePoll
from presentator.ports.decks import BuildRun
from tests.application.fakes import (
    BUILDS_ROOT,
    FakeBuildRunner,
    FakeDeckFolders,
    FakeDeckStore,
    FakeSourceStore,
)
from tests.application.test_decks import (
    DecksFakes,
    a_folder,
    a_source,
    decks_over,
    having,
)

_SOURCE = a_source("decks", identifier="the-configured-source")
_ANOTHER_SOURCE = a_source("talks", identifier="a-second-source")
_NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)
_OWNER = "the account that set the instance up"
_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_A_LATER_COMMIT = "b7c1d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f80"
_A_DECK = "kundenfeedback"


def a_row(slug: str, *, source: Source = _SOURCE, build: Build | None = None) -> Deck:
    return Deck(
        slug=slug,
        title="A talk",
        changed_at=_NOW,
        owner_id=_OWNER,
        source_id=source.id,
        commit=_COMMIT,
        build=build,
        attempt=None,
    )


def a_build_in(talk_run: str, pdf_run: str | None = None) -> Build:
    return Build(
        directory=BUILDS_ROOT / talk_run / "out" / "talk",
        pdf=BUILDS_ROOT / (pdf_run or talk_run) / "out" / "deck.pdf",
        commit=_COMMIT,
        built_at=_NOW,
    )


def runs_left(builder: FakeBuildRunner) -> set[str]:
    return {run.name for run in builder.on_disk}


def _confirmed(decks: Decks, name: str) -> object:
    preview = decks.removal(name)
    assert preview is not None
    return decks.remove_source(
        name,
        expected_deck_count=preview.deck_count,
        expected_run_count=preview.run_count,
    )


def laid_down(builder: FakeBuildRunner, *names: str) -> None:
    builder.on_disk.update(BuildRun(name) for name in names)


def over_rows(
    *rows: Deck,
    on_disk: tuple[str, ...],
    sources: tuple[Source, ...] = (_SOURCE,),
) -> tuple[FakeBuildRunner, FakeDeckStore, FakeDeckFolders, FakeSourceStore]:
    builder = FakeBuildRunner()
    laid_down(builder, *on_disk)
    store = FakeDeckStore()
    for row in rows:
        store.kept[row.slug] = row
    mirror = FakeDeckFolders(carried={source.id: () for source in sources})
    return builder, store, mirror, having(*sources)


def swept(
    *rows: Deck,
    on_disk: tuple[str, ...],
    sources: tuple[Source, ...] = (_SOURCE,),
) -> set[str]:
    builder, store, mirror, source_store = over_rows(
        *rows, on_disk=on_disk, sources=sources
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )
    decks.refresh()
    return runs_left(builder)


def test_a_superseded_build_goes_in_the_pass_that_switched_over() -> None:
    """#505 matrix: superseded after a good switch."""
    folders = FakeDeckFolders(carried={_SOURCE.id: (a_folder(_A_DECK),)})
    builder = FakeBuildRunner()
    store = FakeDeckStore()
    decks = decks_over(mirror=folders, fakes=DecksFakes(builder=builder, store=store))
    decks.refresh()
    first = store.kept[_A_DECK].build
    assert first is not None
    assert runs_left(builder) == {"run-1"}

    folders.carried[_SOURCE.id] = (a_folder(_A_DECK, commit=_A_LATER_COMMIT),)
    decks.refresh()

    now = store.kept[_A_DECK].build
    assert now is not None
    assert now.commit == _A_LATER_COMMIT
    assert runs_left(builder) == {"run-2"}


def test_a_failed_build_leaves_the_standing_one_and_loses_its_leftover() -> None:
    """#505 matrix: failed build, old stays and its leftover goes."""
    folders = FakeDeckFolders(carried={_SOURCE.id: (a_folder(_A_DECK),)})
    builder = FakeBuildRunner()
    decks = decks_over(mirror=folders, fakes=DecksFakes(builder=builder))
    decks.refresh()
    builder.fails = True
    laid_down(builder, "half-written")
    folders.carried[_SOURCE.id] = (a_folder(_A_DECK, commit=_A_LATER_COMMIT),)

    decks.refresh()

    assert runs_left(builder) == {"run-1"}


def test_a_build_the_holds_refused_is_taken_away_and_the_standing_one_stays() -> None:
    """#505 matrix: holds refused, old stays and the refused run goes."""
    folders = FakeDeckFolders(carried={_SOURCE.id: (a_folder(_A_DECK),)})
    builder = FakeBuildRunner()
    decks = decks_over(mirror=folders, fakes=DecksFakes(builder=builder))
    decks.refresh()
    builder.writes_outside_the_root = True
    folders.carried[_SOURCE.id] = (a_folder(_A_DECK, commit=_A_LATER_COMMIT),)

    decks.refresh()

    assert runs_left(builder) == {"run-1"}


def test_a_run_no_row_names_goes_and_the_one_a_row_names_stays() -> None:
    """#505 matrix: crash leftover (run, no row)."""
    assert swept(
        a_row("kept", build=a_build_in("delivered")),
        on_disk=("delivered", "crashed"),
    ) == {"delivered"}


def test_two_decks_of_different_sources_keep_their_own_runs() -> None:
    """#505 matrix: two decks on different sources."""
    assert swept(
        a_row("one", build=a_build_in("first")),
        a_row("two", source=_ANOTHER_SOURCE, build=a_build_in("second")),
        on_disk=("first", "second", "leftover"),
        sources=(_SOURCE, _ANOTHER_SOURCE),
    ) == {"first", "second"}


def test_a_deck_marked_removed_keeps_its_run() -> None:
    """#505 matrix: marked row keeps its run."""
    folders = FakeDeckFolders(carried={_SOURCE.id: (a_folder(_A_DECK),)})
    builder = FakeBuildRunner()
    store = FakeDeckStore()
    decks = decks_over(mirror=folders, fakes=DecksFakes(builder=builder, store=store))
    decks.refresh()

    folders.carried[_SOURCE.id] = ()
    decks.refresh()

    assert store.removed == {_A_DECK}
    assert runs_left(builder) == {"run-1"}


def test_a_talk_and_its_pdf_in_different_runs_keep_both() -> None:
    """#505 matrix: a PDF in a different run from its talk."""
    assert swept(
        a_row("split", build=a_build_in("talk-run", "pdf-run")),
        on_disk=("talk-run", "pdf-run", "leftover"),
    ) == {"talk-run", "pdf-run"}


def test_without_any_row_every_run_goes() -> None:
    """#505 matrix: no row naming any run."""
    assert swept(on_disk=("one", "two")) == set()


def test_a_run_that_would_not_go_is_tried_again_by_the_next_pass() -> None:
    """#505 matrix: removal fails once then succeeds next pass."""
    builder, store, mirror, source_store = over_rows(on_disk=("stuck",))
    builder.stuck = frozenset({BuildRun("stuck")})
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )
    decks.refresh()
    assert runs_left(builder) == {"stuck"}

    builder.stuck = frozenset()
    decks.refresh()

    assert runs_left(builder) == set()


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param(
            Build(
                directory=BUILDS_ROOT.parent / "elsewhere" / "run" / "out" / "talk",
                pdf=BUILDS_ROOT.parent / "elsewhere" / "run" / "out" / "deck.pdf",
                commit=_COMMIT,
                built_at=_NOW,
            ),
            id="outside-the-root",
        ),
        pytest.param(
            Build(
                directory=BUILDS_ROOT / "run" / "commit" / "talk",
                pdf=BUILDS_ROOT / "run" / "commit" / "deck.pdf",
                commit=_COMMIT,
                built_at=_NOW,
            ),
            id="wrongly-shaped-inside-the-root",
        ),
    ],
)
def test_a_row_naming_a_path_no_run_owns_stops_the_pass_without_deleting(
    stored: Build, caplog: pytest.LogCaptureFixture
) -> None:
    """#505 matrix: row outside the root, wrongly shaped path inside it."""
    with caplog.at_level(logging.WARNING):
        left = swept(
            a_row("odd", build=stored),
            a_row("fine", build=a_build_in("delivered")),
            on_disk=("delivered", "other"),
        )

    assert left == {"delivered", "other"}
    assert "is not a run under the builds root" in caplog.text


def test_a_listing_that_did_not_finish_deletes_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#505 matrix: root unlisted or missing."""
    builder, store, mirror, source_store = over_rows(on_disk=("one",))
    builder.listing_fails = True
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    with caplog.at_level(logging.WARNING):
        decks.refresh()

    assert runs_left(builder) == {"one"}
    assert "nothing is swept" in caplog.text


@dataclass
class _FoldersThatBreakOnce(FakeDeckFolders):
    breaking: bool = True

    def folders(self, source: Source) -> SourcePoll:
        if self.breaking:
            raise ConnectionAbortedError
        return super().folders(source)


def test_work_that_raises_skips_the_sweep_and_the_next_pass_catches_up() -> None:
    """#505 matrix: work that raises before the sweep."""
    mirror = _FoldersThatBreakOnce(carried={_SOURCE.id: ()})
    builder = FakeBuildRunner()
    laid_down(builder, "leftover")
    decks = decks_over(mirror=mirror, fakes=DecksFakes(builder=builder))
    with pytest.raises(ConnectionAbortedError):
        decks.refresh()
    assert runs_left(builder) == {"leftover"}

    mirror.breaking = False
    decks.refresh()

    assert runs_left(builder) == set()


def test_refreshing_one_source_sweeps_too() -> None:
    """#505 matrix: refresh_named sweeps like refresh."""
    builder, store, mirror, source_store = over_rows(on_disk=("leftover",))
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    assert decks.refresh_named(_SOURCE.name)

    assert runs_left(builder) == set()


@dataclass
class _SourceRemovedAfterTheFirstLook(FakeSourceStore):
    looks: int = 0

    def all(self) -> tuple[Source, ...]:
        self.looks += 1
        return super().all() if self.looks == 1 else ()


def test_a_source_removed_before_the_lock_is_taken_gets_no_rows_written() -> None:
    """#505 matrix: refresh_named with its source removed before the lock."""
    sources = _SourceRemovedAfterTheFirstLook(sources=[_SOURCE])
    store = FakeDeckStore()
    decks = decks_over(
        sources=sources,
        mirror=FakeDeckFolders(carried={_SOURCE.id: (a_folder(_A_DECK),)}),
        fakes=DecksFakes(store=store),
    )

    assert decks.refresh_named(_SOURCE.name)

    assert store.kept == {}


def test_removing_a_source_takes_the_runs_of_its_talk_and_its_pdf() -> None:
    """#505 matrix: remove_source takes the runs its rows name."""
    builder, store, mirror, source_store = over_rows(
        a_row("split", build=a_build_in("talk-run", "pdf-run")),
        on_disk=("talk-run", "pdf-run", "foreign"),
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    _confirmed(decks, _SOURCE.name)

    assert runs_left(builder) == {"foreign"}
    assert store.kept == {}


def test_removing_a_source_over_a_build_no_run_owns_keeps_every_row() -> None:
    """#505 matrix: remove_source over an unplaced build answers DISK_LEFTOVER."""
    builder, store, mirror, source_store = over_rows(
        a_row(
            "odd",
            build=Build(
                directory=BUILDS_ROOT / "talk",
                pdf=BUILDS_ROOT / "deck.pdf",
                commit=_COMMIT,
                built_at=_NOW,
            ),
        ),
        on_disk=("other",),
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    outcome = _confirmed(decks, _SOURCE.name)

    assert outcome is SourceRemovalRefusal.DISK_LEFTOVER
    assert set(store.kept) == {"odd"}
    assert source_store.sources == [_SOURCE]
    assert runs_left(builder) == {"other"}
