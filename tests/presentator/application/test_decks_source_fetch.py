"""What a fetch now answers: the run it recorded and what it changed."""

from dataclasses import dataclass
from datetime import timedelta
from threading import Thread

from presentator.application.decks import Decks
from presentator.contracts.decks import (
    Source,
    SourceFetch,
    SourcePoll,
    SourceRunFailure,
    SourceRunOutcome,
)
from tests.presentator.application.support.decks_support import (
    SHORT_COMMIT,
    SOURCE,
    DecksFakes,
    a_folder,
    carrying,
    decks_over,
)
from tests.presentator.application.support.fakes import (
    PATIENCE,
    FakeBuildRunner,
    FakeDeckFolders,
    FrozenClock,
    HeldDeckFolders,
)


def _fetched(mirror: FakeDeckFolders, builder: FakeBuildRunner) -> Decks:
    return decks_over(mirror=mirror, fakes=DecksFakes(builder=builder))


def _fetch(decks: Decks) -> SourceFetch:
    fetched = decks.refresh_named(SOURCE.name)
    assert fetched is not None
    return fetched


def test_fetching_a_changed_deck_answers_the_run_and_one_build() -> None:
    mirror = carrying(a_folder("kundenfeedback"))
    decks = _fetched(mirror, FakeBuildRunner())

    fetched = _fetch(decks)

    assert fetched.run is not None
    assert fetched.run.outcome is SourceRunOutcome.SUCCESS
    assert fetched.run.commit == SHORT_COMMIT
    assert (fetched.decks_built, fetched.builds_failed, fetched.decks_removed) == (
        1,
        0,
        0,
    )


def test_fetching_a_deck_that_cannot_build_answers_one_failed_build() -> None:
    decks = _fetched(carrying(a_folder("kundenfeedback")), FakeBuildRunner(fails=True))

    fetched = _fetch(decks)

    assert fetched.run is not None
    assert fetched.run.outcome is SourceRunOutcome.SUCCESS
    assert (fetched.decks_built, fetched.builds_failed) == (0, 1)


def test_fetching_after_a_folder_was_deleted_answers_one_removed_deck() -> None:
    mirror = carrying(a_folder("alter-vortrag"), a_folder("kundenfeedback"))
    decks = _fetched(mirror, FakeBuildRunner())
    _fetch(decks)

    mirror.carried[SOURCE.id] = (a_folder("kundenfeedback"),)
    fetched = _fetch(decks)

    assert fetched.run is not None
    assert fetched.run.outcome is SourceRunOutcome.SUCCESS
    assert fetched.decks_removed == 1
    assert (fetched.decks_built, fetched.builds_failed) == (0, 0)


def test_fetching_nothing_new_answers_a_success_with_every_count_zero() -> None:
    decks = _fetched(carrying(a_folder("kundenfeedback")), FakeBuildRunner())
    _fetch(decks)

    fetched = _fetch(decks)

    assert fetched.run is not None
    assert fetched.run.outcome is SourceRunOutcome.SUCCESS
    assert (fetched.decks_built, fetched.builds_failed, fetched.decks_removed) == (
        0,
        0,
        0,
    )


def test_fetching_a_source_that_cannot_be_read_answers_the_failure_and_its_reason() -> (
    None
):
    mirror = FakeDeckFolders(
        carried={SOURCE.id: None},
        failures={SOURCE.id: SourceRunFailure.CREDENTIAL_UNRESOLVABLE},
    )

    fetched = _fetch(_fetched(mirror, FakeBuildRunner()))

    assert fetched.run is not None
    assert fetched.run.outcome is SourceRunOutcome.FAILURE
    assert fetched.run.reason is SourceRunFailure.CREDENTIAL_UNRESOLVABLE
    assert (fetched.decks_built, fetched.builds_failed, fetched.decks_removed) == (
        0,
        0,
        0,
    )


def test_fetching_while_another_refresh_holds_the_lock_answers_no_run() -> None:
    held = HeldDeckFolders(found=(a_folder("kundenfeedback"),))
    decks = decks_over(mirror=held)
    running = Thread(target=decks.refresh)
    running.start()
    assert held.entered.wait(PATIENCE.total_seconds())

    fetched = _fetch(decks)

    held.release.set()
    running.join(PATIENCE.total_seconds())
    assert fetched == SourceFetch(
        run=None, decks_built=0, builds_failed=0, decks_removed=0
    )


@dataclass
class _SlowFolders(FakeDeckFolders):
    """A mirror whose pull takes this long on the clock."""

    clock: FrozenClock | None = None
    pull_takes: timedelta = timedelta(seconds=40)

    def folders(self, source: Source) -> SourcePoll:
        assert self.clock is not None
        self.clock.advance(self.pull_takes)
        return super().folders(source)


def test_a_slow_pull_records_the_run_at_the_instant_the_pass_began() -> None:
    fakes = DecksFakes()
    mirror = _SlowFolders(
        carried={SOURCE.id: (a_folder("kundenfeedback"),)}, clock=fakes.clock
    )
    began = fakes.clock.instant
    decks = decks_over(mirror=mirror, fakes=fakes)

    decks.refresh()

    run = fakes.source_runs.newest(SOURCE.id)
    assert run is not None
    assert run.at == began
