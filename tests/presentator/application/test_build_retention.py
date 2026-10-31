"""Which runs of the build a refresh pass leaves on disk: those a deck row names."""

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self

import pytest

from presentator.application.build_retention import REMOVED_DECK_KEPT
from presentator.application.decks import (
    Decks,
    SourceRemovalRefusal,
    SourceRemovalStale,
)
from presentator.contracts.decks import (
    MANIFEST_FILE,
    Build,
    Deck,
    DeckFolder,
    Source,
    SourcePoll,
)
from presentator.ports.decks import BuildRun
from tests.presentator.application.support.decks_support import (
    DecksFakes,
    a_folder,
    a_source,
    decks_over,
    having,
)
from tests.presentator.application.support.fakes import (
    BUILDS_ROOT,
    FakeBuildRunner,
    FakeDeckFolders,
    FakeDeckStore,
    FakeSourceStore,
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

    assert set(store.removed) == {_A_DECK}
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
def test_a_row_naming_a_path_no_run_owns_stops_the_sweep_without_deleting(
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


_DAY = timedelta(days=1)
_KEPT = REMOVED_DECK_KEPT.days
_SECOND = timedelta(seconds=1)
_RETENTION_LOG = "presentator.application.build_retention"


@dataclass
class _Instance:
    """Decks over fakes whose mirror, store, disk and clock a test moves."""

    sources: tuple[Source, ...] = (_SOURCE,)
    fakes: DecksFakes = field(default_factory=DecksFakes)
    folders: FakeDeckFolders = field(default_factory=FakeDeckFolders)
    decks: Decks = field(init=False)

    def __post_init__(self) -> None:
        self.decks = decks_over(
            sources=having(*self.sources), mirror=self.folders, fakes=self.fakes
        )

    def carries(self, *folders: DeckFolder, source: Source = _SOURCE) -> Self:
        self.folders.carried[source.id] = folders
        return self

    def cannot_read(self, source: Source = _SOURCE) -> None:
        self.folders.carried[source.id] = None

    def on_day(self, day: int, *, early_by: timedelta = timedelta()) -> None:
        """Refresh that many days after the day-0 pass, or that much earlier."""
        self.fakes.clock.instant = _NOW + day * _DAY - early_by
        self.decks.refresh()

    def address_of(self, slug: str) -> str | None:
        """The source address that slug delivers from, or nothing."""
        page = self.decks.page(slug, viewer=_OWNER)
        return None if page is None else page.source

    @property
    def store(self) -> FakeDeckStore:
        return self.fakes.store

    @property
    def runs(self) -> set[str]:
        return runs_left(self.fakes.builder)


def missed_on_day_zero() -> _Instance:
    """A deck built from its folder, whose folder the next pass, day 0, missed."""
    instance = _Instance().carries(a_folder(_A_DECK))
    instance.on_day(0)
    instance.carries()
    instance.on_day(0)
    assert instance.runs == {"run-1"}
    return instance


def test_a_deck_missed_for_thirty_days_goes_with_its_run_not_a_second_sooner() -> None:
    """#511 matrix 1."""
    instance = missed_on_day_zero()

    instance.on_day(_KEPT, early_by=_SECOND)
    assert set(instance.store.kept) == {_A_DECK}
    assert instance.runs == {"run-1"}

    instance.on_day(_KEPT)
    assert instance.store.kept == {}
    assert instance.runs == set()


def test_a_renamed_folder_is_a_new_deck_and_its_old_name_goes_after_thirty_days() -> (
    None
):
    """#511 matrix 25."""
    instance = _Instance().carries(a_folder(_A_DECK))
    instance.on_day(0)
    instance.carries(a_folder("kundenfeedback-q4"))

    instance.on_day(0)
    assert instance.address_of("kundenfeedback-q4") == _SOURCE.url
    assert instance.address_of(_A_DECK) is None
    assert instance.runs == {"run-1", "run-2"}

    instance.on_day(_KEPT)
    assert set(instance.store.kept) == {"kundenfeedback-q4"}
    assert instance.runs == {"run-2"}


def test_a_second_miss_keeps_counting_from_the_first() -> None:
    """#511 matrix 2."""
    instance = missed_on_day_zero()
    instance.on_day(10)

    instance.on_day(_KEPT)

    assert instance.store.kept == {}


def test_a_folder_back_before_the_thirty_days_is_the_deck_it_was() -> None:
    """#511 matrix 3: back on day 29, missed again on day 40, gone on day 70."""
    instance = missed_on_day_zero()
    before = instance.store.kept[_A_DECK]
    instance.carries(a_folder(_A_DECK))

    instance.on_day(_KEPT - 1)
    assert instance.store.kept[_A_DECK] == before
    assert instance.store.removed == {}
    assert instance.runs == {"run-1"}

    instance.carries()
    instance.on_day(_KEPT + 10)
    instance.on_day(2 * _KEPT + 10, early_by=_SECOND)
    assert set(instance.store.kept) == {_A_DECK}

    instance.on_day(2 * _KEPT + 10)
    assert instance.store.kept == {}
    assert instance.runs == set()


def test_a_folder_back_while_nothing_ran_is_kept_by_the_first_pass_after() -> None:
    """#511 matrix 4: back on day 20, the service down until day 31."""
    instance = missed_on_day_zero()
    instance.carries(a_folder(_A_DECK))

    instance.on_day(_KEPT + 1)

    assert set(instance.store.kept) == {_A_DECK}
    assert instance.runs == {"run-1"}


def test_a_folder_pushed_again_after_the_purge_is_a_new_deck_built_at_once() -> None:
    """#511 matrix 5."""
    instance = missed_on_day_zero()
    instance.on_day(_KEPT)
    instance.carries(a_folder(_A_DECK))

    instance.on_day(_KEPT + 1)

    again = instance.store.kept[_A_DECK]
    assert again.owner_id == _OWNER
    assert again.build is not None
    assert instance.runs == {"run-2"}


@pytest.mark.parametrize(
    ("carried_on_day_40", "kept"),
    [
        pytest.param((), set[str](), id="still-missing"),
        pytest.param((a_folder(_A_DECK),), {_A_DECK}, id="back"),
    ],
)
def test_an_unreadable_source_deletes_nothing_until_it_reads_again(
    carried_on_day_40: tuple[DeckFolder, ...], kept: set[str]
) -> None:
    """#511 matrix 6: unreadable from day 5 to day 40."""
    instance = missed_on_day_zero()
    instance.cannot_read()
    for day in (5, _KEPT, _KEPT + 9):
        instance.on_day(day)
    assert set(instance.store.kept) == {_A_DECK}
    assert instance.runs == {"run-1"}

    instance.carries(*carried_on_day_40)
    instance.on_day(_KEPT + 10)

    assert set(instance.store.kept) == kept
    assert instance.runs == ({"run-1"} if kept else set())


def test_an_emptied_branch_takes_every_deck_of_its_source_after_thirty_days() -> None:
    """#511 matrix 7."""
    instance = _Instance().carries(a_folder(_A_DECK), a_folder("knowledge-fabric"))
    instance.on_day(0)
    instance.carries()
    instance.on_day(0)

    instance.on_day(_KEPT)

    assert instance.store.kept == {}
    assert instance.runs == set()


def test_one_sources_purge_leaves_an_unreadable_sources_marks_alone() -> None:
    """#511 matrix 8."""
    instance = _Instance(sources=(_SOURCE, _ANOTHER_SOURCE))
    instance.carries(a_folder(_A_DECK))
    instance.carries(a_folder("their-talk"), source=_ANOTHER_SOURCE)
    instance.on_day(0)
    instance.carries()
    instance.carries(source=_ANOTHER_SOURCE)
    instance.on_day(0)
    instance.cannot_read(_ANOTHER_SOURCE)

    instance.on_day(_KEPT)

    assert set(instance.store.kept) == {"their-talk"}


def test_refreshing_one_source_deletes_its_decks_missed_thirty_days() -> None:
    """#511 matrix 9."""
    instance = missed_on_day_zero()
    instance.fakes.clock.instant = _NOW + 30 * _DAY

    assert instance.decks.refresh_named(_SOURCE.name)

    assert instance.store.kept == {}
    assert instance.runs == set()


@pytest.mark.parametrize(
    "still_listed",
    [
        pytest.param(a_folder(_A_DECK, title=None), id="manifest-unreadable"),
        pytest.param(
            a_folder(_A_DECK, file_names=frozenset({MANIFEST_FILE})),
            id="slides-missing",
        ),
    ],
)
def test_a_folder_still_in_the_tree_keeps_its_deck(still_listed: DeckFolder) -> None:
    """#511 matrix 10."""
    instance = _Instance().carries(a_folder(_A_DECK))
    instance.on_day(0)
    instance.carries(still_listed)

    for day in (0, _KEPT + 1, 2 * _KEPT):
        instance.on_day(day)

    assert set(instance.store.kept) == {_A_DECK}
    assert instance.runs == {"run-1"}


def a_month_after_a_miss(*rows: Deck, carried: tuple[DeckFolder, ...]) -> _Instance:
    """Those rows, the first marked at _NOW, and the folders the source carries."""
    instance = _Instance().carries(*carried)
    for row in rows:
        instance.store.kept[row.slug] = row
    instance.store.removed[rows[0].slug] = _NOW
    return instance


def test_a_purged_rows_run_another_row_names_stays() -> None:
    """#511 matrix 11."""
    instance = a_month_after_a_miss(
        a_row("gone", build=a_build_in("shared")),
        a_row("kept", build=a_build_in("shared")),
        carried=(a_folder("kept"),),
    )
    laid_down(instance.fakes.builder, "shared")

    instance.on_day(_KEPT)

    assert set(instance.store.kept) == {"kept"}
    assert instance.runs == {"shared"}


def test_a_refused_sweep_leaves_the_purged_rows_run_to_the_next_pass() -> None:
    """#511 matrix 12."""
    unplaced = Build(
        directory=BUILDS_ROOT / "talk",
        pdf=BUILDS_ROOT / "deck.pdf",
        commit=_COMMIT,
        built_at=_NOW,
    )
    instance = a_month_after_a_miss(
        a_row("gone", build=a_build_in("old")),
        a_row("odd", build=unplaced),
        carried=(a_folder("odd"),),
    )
    laid_down(instance.fakes.builder, "old")

    instance.on_day(_KEPT)
    assert set(instance.store.kept) == {"odd"}
    assert instance.runs == {"old"}

    instance.store.put_build("odd", a_build_in("placed"))
    laid_down(instance.fakes.builder, "placed")
    instance.on_day(_KEPT + 1)
    assert instance.runs == {"placed"}


def test_a_purged_row_without_a_build_touches_no_run() -> None:
    """#511 matrix 13."""
    instance = a_month_after_a_miss(a_row("never-built"), carried=())

    instance.on_day(_KEPT)

    assert instance.store.kept == {}
    assert instance.fakes.builder.removed == []


def a_name_both_sources_carry(*order: Source) -> _Instance:
    """_SOURCE's deck missed on day 0, the other source carrying its name since."""
    instance = _Instance(sources=order)
    instance.carries(a_folder(_A_DECK))
    instance.carries(source=_ANOTHER_SOURCE)
    instance.on_day(0)
    instance.carries()
    instance.carries(a_folder(_A_DECK), source=_ANOTHER_SOURCE)
    instance.on_day(0)
    return instance


@pytest.mark.parametrize(
    ("order", "at_expiry"),
    [
        pytest.param((_SOURCE, _ANOTHER_SOURCE), _ANOTHER_SOURCE.url, id="same-pass"),
        pytest.param((_ANOTHER_SOURCE, _SOURCE), None, id="next-pass"),
    ],
)
def test_another_source_takes_a_purged_address_and_not_before(
    order: tuple[Source, Source],
    at_expiry: str | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#511 matrix 14."""
    with caplog.at_level(logging.WARNING):
        instance = a_name_both_sources_carry(*order)
        instance.on_day(_KEPT - 1)
    assert "already carried by another source" in caplog.text
    assert set(instance.store.removed) == {_A_DECK}

    instance.on_day(_KEPT)
    assert instance.address_of(_A_DECK) == at_expiry

    instance.on_day(_KEPT + 1)
    assert instance.address_of(_A_DECK) == _ANOTHER_SOURCE.url
    assert instance.decks.built_talk(_A_DECK, viewer=_OWNER) is not None


def test_a_re_push_after_another_source_took_the_address_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#511 matrix 15."""
    instance = a_name_both_sources_carry(_SOURCE, _ANOTHER_SOURCE)
    instance.on_day(_KEPT)
    instance.carries(a_folder(_A_DECK))
    caplog.clear()

    with caplog.at_level(logging.WARNING):
        instance.on_day(_KEPT + 1)

    assert "already carried by another source" in caplog.text
    assert instance.address_of(_A_DECK) == _ANOTHER_SOURCE.url


def test_a_removal_confirmed_before_a_purge_is_stale() -> None:
    """#511 matrix 16."""
    instance = missed_on_day_zero()
    instance.on_day(_KEPT - 1)
    before = instance.decks.removal(_SOURCE.name)
    assert before is not None
    instance.on_day(_KEPT)
    runs_now = instance.decks.removal(_SOURCE.name)
    assert runs_now is not None

    outcome = instance.decks.remove_source(
        _SOURCE.name,
        expected_deck_count=before.deck_count,
        expected_run_count=runs_now.run_count,
    )

    assert isinstance(outcome, SourceRemovalStale)
    assert outcome.current.deck_count == 0


def test_a_purge_names_its_source_and_count_in_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#511 matrix 17."""
    instance = missed_on_day_zero()

    def said_on(day: int) -> list[str]:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=_RETENTION_LOG):
            instance.on_day(day)
        return [
            record.getMessage()
            for record in caplog.records
            if record.name == _RETENTION_LOG and record.levelno == logging.INFO
        ]

    assert said_on(_KEPT - 1) == []
    (line,) = said_on(_KEPT)
    assert _SOURCE.id in line
    assert re.search(r"\b1\b", line)
    assert _A_DECK not in line
