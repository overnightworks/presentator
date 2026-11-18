"""One refresh at a time across every process of the instance, and what it leaves."""

from datetime import timedelta

import pytest

from presentator.contracts.decks import BuildRun
from presentator.ports.decks import RefreshOwnershipTimedOutError
from tests.presentator.application.support.decks_support import (
    NOW,
    SOURCE,
    DecksFakes,
    a_folder,
    decks_over,
)
from tests.presentator.application.support.fakes import (
    FakeRefreshOwnership,
    FrozenClock,
)


def test_a_refresh_another_process_holds_takes_nothing_in_and_a_fetch_says_so() -> None:
    fakes = DecksFakes(ownership=FakeRefreshOwnership(held_elsewhere=True))
    decks = decks_over(a_folder("kundenfeedback"), fakes=fakes)

    decks.refresh()
    fetched = decks.refresh_named(SOURCE.name)

    assert fakes.source_runs.recent(SOURCE.id) == ()
    assert fetched is not None
    assert fetched.run is None


def test_a_refresh_that_lost_the_refresh_before_its_sweep_sweeps_nothing() -> None:
    fakes = DecksFakes(ownership=FakeRefreshOwnership(lost_to_another=True))
    decks = decks_over(a_folder("kundenfeedback"), fakes=fakes)
    fakes.builder.on_disk.add(BuildRun("a-run-nobody-names"))

    decks.refresh()

    assert fakes.builder.removed == []


def test_a_source_polled_within_the_interval_is_left_to_the_next_refresh() -> None:
    clock = FrozenClock(instant=NOW)
    fakes = DecksFakes(clock=clock, poll_interval=timedelta(minutes=5))
    decks = decks_over(a_folder("kundenfeedback"), fakes=fakes)
    decks.refresh()

    clock.advance(by=timedelta(minutes=4))
    decks.refresh()
    runs_inside_the_interval = len(fakes.source_runs.recent(SOURCE.id))
    clock.advance(by=timedelta(minutes=1))
    decks.refresh()

    assert runs_inside_the_interval == 1
    assert len(fakes.source_runs.recent(SOURCE.id)) == 2  # noqa: PLR2004 - two polls


def test_a_source_polled_within_the_interval_is_still_fetched_by_name() -> None:
    fakes = DecksFakes(poll_interval=timedelta(minutes=5))
    decks = decks_over(a_folder("kundenfeedback"), fakes=fakes)
    decks.refresh()

    fetched = decks.refresh_named(SOURCE.name)

    assert fetched is not None
    assert fetched.run is not None
    assert len(fakes.source_runs.recent(SOURCE.id)) == 2  # noqa: PLR2004 - two polls


def test_a_removal_that_waits_past_its_bound_for_the_refresh_deletes_nothing() -> None:
    fakes = DecksFakes(ownership=FakeRefreshOwnership(held_elsewhere=True))
    decks = decks_over(a_folder("kundenfeedback"), fakes=fakes)

    with pytest.raises(RefreshOwnershipTimedOutError):
        decks.remove_source(SOURCE.name, expected_deck_count=0, expected_run_count=0)

    assert [source.name for source in decks.sources.all()] == [SOURCE.name]
