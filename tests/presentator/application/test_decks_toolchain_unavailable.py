"""A build the worker never started is no attempt at the deck."""

import pytest

from presentator.application.decks import Decks
from presentator.contracts.decks import (
    BuildFailure,
    DeckPage,
    DeckState,
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
