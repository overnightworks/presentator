"""A build the worker never started is no attempt; a changed toolchain builds again."""

from dataclasses import replace

import pytest

from presentator.application.decks import Decks
from presentator.contracts.decks import (
    Build,
    BuildFailure,
    DeckPage,
    DeckState,
    ToolchainIdentity,
)
from tests.presentator.application.support.decks_support import (
    BUILD_BOUND,
    OWNER,
    SHORT_COMMIT,
    SOURCE,
    DecksFakes,
    a_folder,
    a_source,
    a_store_that_was_left_building,
    carrying,
    decks_over,
    having,
)
from tests.presentator.application.support.fakes import (
    FakeBuildRunner,
    FakeDeckFolders,
    FakeDeckStore,
)

_ANOTHER_SOURCE = a_source("talks", identifier="a-second-source")
_A_LATER_COMMIT = "b7c1d9e0f1a2b3c4d5e6f708192a3b4c5d6e7f80"
_WHAT_THE_TOOLCHAIN_SAID = "slides.md:41:3 Unexpected token in frontmatter"
_BEFORE = ToolchainIdentity("a" * 64)
_AFTER = ToolchainIdentity("b" * 64)
_SLUG = "kundenfeedback"


def page_of(decks: Decks, slug: str) -> DeckPage:
    page = decks.page(slug, viewer=OWNER)
    assert page is not None
    return page


def listed_states(decks: Decks) -> list[DeckState]:
    return [deck.state for deck in decks.listed(viewer=OWNER)]


def both_decks_moved_on(mirror: FakeDeckFolders) -> None:
    mirror.carried[SOURCE.id] = (
        a_folder("kundenfeedback", commit=_A_LATER_COMMIT),
        a_folder("knowledge-fabric", commit=_A_LATER_COMMIT),
    )


@pytest.mark.parametrize(
    "failed_before",
    [False, True],
    ids=["no attempt before", "a failed attempt at an older commit before"],
)
def test_a_build_the_worker_never_started_leaves_the_deck_as_it_was_and_is_tried_again(
    *, failed_before: bool
) -> None:
    builder = FakeBuildRunner(fails=failed_before, says=_WHAT_THE_TOOLCHAIN_SAID)
    store = FakeDeckStore()
    mirror = carrying(a_folder("kundenfeedback"), a_folder("knowledge-fabric"))
    decks = decks_over(mirror=mirror, fakes=DecksFakes(store=store, builder=builder))
    if failed_before:
        decks.refresh()
    before = {
        slug: next((deck.attempt for deck in store.all() if deck.slug == slug), None)
        for slug in ("kundenfeedback", "knowledge-fabric")
    }
    builder.fails = False
    builder.unavailable = True
    builder.built.clear()
    if failed_before:
        both_decks_moved_on(mirror)

    decks.refresh()

    assert {deck.slug: deck.attempt for deck in store.all()} == before
    assert len(builder.built) == 1
    builder.unavailable = False
    decks.refresh()
    assert sorted(builder.built[1:]) == ["knowledge-fabric", "kundenfeedback"]
    assert listed_states(decks) == [DeckState.READY, DeckState.READY]


def test_a_build_the_worker_never_started_is_counted_as_failed_not_as_nothing_new() -> (
    None
):
    builder = FakeBuildRunner(unavailable=True)
    decks = decks_over(a_folder("kundenfeedback"), fakes=DecksFakes(builder=builder))

    fetched = decks.refresh_named(SOURCE.name)

    assert fetched is not None
    assert (fetched.decks_built, fetched.builds_failed) == (0, 1)


def test_a_worker_that_does_not_answer_stops_the_builds_of_every_source() -> None:
    builder = FakeBuildRunner(unavailable=True)
    store = FakeDeckStore()
    mirror = FakeDeckFolders(
        carried={
            SOURCE.id: (a_folder("kundenfeedback"),),
            _ANOTHER_SOURCE.id: (a_folder("knowledge-fabric"),),
        },
    )
    decks = decks_over(
        sources=having(SOURCE, _ANOTHER_SOURCE),
        mirror=mirror,
        fakes=DecksFakes(store=store, builder=builder),
    )

    decks.refresh()

    assert builder.built == ["kundenfeedback"]
    assert sorted(deck.slug for deck in store.all()) == [
        "knowledge-fabric",
        "kundenfeedback",
    ]
    assert all(deck.attempt is None and deck.build is None for deck in store.all())
    builder.unavailable = False
    decks.refresh()
    assert sorted(builder.built[1:]) == ["knowledge-fabric", "kundenfeedback"]


def test_a_deck_page_says_building_only_while_the_worker_is_asked() -> None:
    states: list[DeckState] = []
    builder = FakeBuildRunner(unavailable=True)
    decks = decks_over(a_folder("kundenfeedback"), fakes=DecksFakes(builder=builder))
    builder.while_building = lambda _deck: states.append(
        page_of(decks, "kundenfeedback").state
    )

    decks.refresh()

    assert states == [DeckState.BUILDING]
    assert page_of(decks, "kundenfeedback").state is DeckState.NEVER_BUILT


def test_an_attempt_given_up_on_stays_failed_when_its_next_build_never_started() -> (
    None
):
    store = a_store_that_was_left_building(started_ago=BUILD_BOUND * 2)
    builder = FakeBuildRunner(unavailable=True)
    decks = decks_over(
        a_folder("kundenfeedback", commit=_A_LATER_COMMIT),
        fakes=DecksFakes(store=store, builder=builder),
    )

    decks.refresh()

    page = page_of(decks, "kundenfeedback")
    assert page.state is DeckState.FAILED
    assert page.attempt is not None
    assert page.attempt.failure == BuildFailure.without_words()
    assert page.attempt.commit == SHORT_COMMIT


def standing_build(store: FakeDeckStore) -> Build:
    build = store.kept[_SLUG].build
    assert build is not None
    return build


def a_deck_built_under_the_toolchain_before() -> tuple[
    Decks, FakeDeckStore, FakeBuildRunner, FakeDeckFolders
]:
    builder = FakeBuildRunner(identity=_BEFORE)
    store = FakeDeckStore()
    mirror = carrying(a_folder(_SLUG))
    decks = decks_over(mirror=mirror, fakes=DecksFakes(store=store, builder=builder))
    decks.refresh()
    assert standing_build(store).toolchain == _BEFORE
    return decks, store, builder, mirror


def the_folder_moves_to(mirror: FakeDeckFolders, commit: str) -> None:
    mirror.carried[SOURCE.id] = (a_folder(_SLUG, commit=commit),)


def test_a_changed_toolchain_builds_a_deck_again_while_its_old_talk_stands() -> None:
    decks, store, builder, _ = a_deck_built_under_the_toolchain_before()
    old_talk = decks.built_talk(_SLUG, viewer=OWNER)
    builder.identity = _AFTER
    seen: list[tuple[DeckState, object]] = []
    builder.while_building = lambda _deck: seen.append(
        (page_of(decks, _SLUG).state, decks.built_talk(_SLUG, viewer=OWNER))
    )

    decks.refresh()

    assert seen == [(DeckState.BUILDING, old_talk)]
    assert builder.built == [_SLUG, _SLUG]
    assert (standing_build(store).commit, standing_build(store).toolchain) == (
        store.kept[_SLUG].commit,
        _AFTER,
    )
    assert decks.built_talk(_SLUG, viewer=OWNER) != old_talk
    assert page_of(decks, _SLUG).state is DeckState.READY


@pytest.mark.parametrize(
    "moves",
    ["nothing", "the commit", "the toolchain"],
)
def test_a_failed_build_keeps_the_old_talk_and_is_tried_again_only_when_it_moves(
    moves: str,
) -> None:
    decks, store, builder, mirror = a_deck_built_under_the_toolchain_before()
    old = standing_build(store)
    builder.identity = _AFTER
    builder.fails = True
    builder.says = _WHAT_THE_TOOLCHAIN_SAID
    decks.refresh()
    page = page_of(decks, _SLUG)
    assert page.state is DeckState.FAILED
    assert page.attempt is not None
    assert page.attempt.failure == BuildFailure.said(_WHAT_THE_TOOLCHAIN_SAID)
    assert standing_build(store) == old
    built_so_far = len(builder.built)
    if moves == "the toolchain":
        builder.identity = ToolchainIdentity("c" * 64)
    if moves == "the commit":
        the_folder_moves_to(mirror, _A_LATER_COMMIT)

    decks.refresh()

    assert len(builder.built) - built_so_far == (0 if moves == "nothing" else 1)
    assert standing_build(store) == old


def test_a_refresh_that_finds_commit_and_toolchain_unchanged_changes_nothing() -> None:
    decks, store, builder, _ = a_deck_built_under_the_toolchain_before()
    before = dict(store.kept)

    decks.refresh()

    assert builder.built == [_SLUG]
    assert store.kept == before


def test_a_talk_built_with_no_known_toolchain_is_built_once() -> None:
    decks, store, builder, _ = a_deck_built_under_the_toolchain_before()
    store.put_build(_SLUG, replace(standing_build(store), toolchain=None))

    decks.refresh()
    decks.refresh()

    assert builder.built == [_SLUG, _SLUG]
    assert standing_build(store).toolchain == _BEFORE


def test_a_failed_attempt_is_tried_once_more_under_another_toolchain() -> None:
    builder = FakeBuildRunner(identity=_BEFORE, fails=True)
    decks = decks_over(a_folder(_SLUG), fakes=DecksFakes(builder=builder))
    decks.refresh()
    decks.refresh()
    assert builder.built == [_SLUG]
    builder.identity = _AFTER
    builder.fails = False

    decks.refresh()

    assert builder.built == [_SLUG, _SLUG]
    assert page_of(decks, _SLUG).state is DeckState.READY


def test_a_revert_to_a_talk_built_under_another_toolchain_builds_it_anew() -> None:
    decks, store, builder, mirror = a_deck_built_under_the_toolchain_before()
    commit_before = store.kept[_SLUG].commit
    builder.identity = _AFTER
    builder.fails = True
    the_folder_moves_to(mirror, _A_LATER_COMMIT)
    decks.refresh()
    assert page_of(decks, _SLUG).state is DeckState.FAILED
    builder.fails = False
    built_so_far = len(builder.built)
    the_folder_moves_to(mirror, commit_before)

    decks.refresh()

    assert len(builder.built) - built_so_far == 1
    assert page_of(decks, _SLUG).state is DeckState.READY
    assert standing_build(store).toolchain == _AFTER
