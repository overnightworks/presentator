"""Erasing a person: everything they own goes, nothing of anyone else."""

import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from threading import RLock

import pytest

from presentator.contracts.decks import Source, SourceRun
from presentator.contracts.models import Role, User
from presentator.contracts.people import (
    KindCount,
    LeftoverDirectory,
    PersonBusy,
    PersonDiskLeftover,
    PersonErased,
    PersonErasure,
    PersonKind,
)
from presentator.ports.decks import BuildRun
from tests.presentator.application.support.decks_support import (
    DecksFakes,
    a_folder,
)
from tests.presentator.application.support.fakes import (
    PATIENCE,
    FakeDeckFolders,
    FakeSourceRunStore,
    FakeSourceStore,
)
from tests.presentator.application.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
    Instance,
    an_instance,
)


def counts(outcome: PersonErasure) -> dict[PersonKind, int]:
    assert isinstance(outcome, PersonErased)
    return {entry.kind: entry.count for entry in outcome.kinds}


@contextmanager
def another_thread_holding(lock: RLock) -> Generator[None]:
    """A refresh under way: some other thread holds the one lock until released."""
    acquired = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with lock:
            acquired.set()
            release.wait(PATIENCE.total_seconds())

    thread = threading.Thread(target=hold)
    thread.start()
    acquired.wait(PATIENCE.total_seconds())
    try:
        yield
    finally:
        release.set()
        thread.join()


_EVERY_KIND_OF_THE_PERSON = {
    PersonKind.ACCOUNT: 1,
    PersonKind.SESSIONS: 1,
    PersonKind.PREFERENCES: 1,
    PersonKind.KEY_DRAFT: 1,
    PersonKind.SOURCES: 2,
    PersonKind.DECKS: 2,
    PersonKind.RUNS: 2,
    PersonKind.MIRRORS: 2,
    PersonKind.BUILDS: 2,
}
_NOTHING = dict.fromkeys(_EVERY_KIND_OF_THE_PERSON, 0)


def test_a_real_run_removes_every_kind_of_row_and_reports_what_went() -> None:
    """S9 (#293): a real run removes every kind of row and reports each kind's count."""
    instance = an_instance()

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert counts(outcome) == _EVERY_KIND_OF_THE_PERSON
    assert THE_PERSON.id not in instance.users.users
    assert instance.sessions.sessions_of(THE_PERSON.id) == ()
    assert instance.preferences.read(THE_PERSON.id) is None
    assert instance.fakes.key_drafts.count_for_owner(THE_PERSON.id) == 0
    assert [source.owner_id for source in instance.decks.sources.all()] == [
        ANOTHER.id,
    ]
    assert [deck.owner_id for deck in instance.fakes.store.kept.values()] == [
        ANOTHER.id,
    ]
    assert {run.source_id for run in instance.fakes.source_runs.recorded} == {
        "notes-id",
    }
    assert instance.folders.forgotten == ["decks-id", "talks-id"]
    assert (
        len(instance.fakes.builder.removed)
        == _EVERY_KIND_OF_THE_PERSON[PersonKind.BUILDS]
    )


def test_a_real_run_leaves_a_second_persons_rows_alone() -> None:
    """E5 (#418): the erasure takes only the named person's rows."""
    instance = an_instance()
    anothers_before = (
        instance.users.users[ANOTHER.id],
        instance.preferences.read(ANOTHER.id),
        instance.fakes.key_drafts.count_for_owner(ANOTHER.id),
        instance.sessions.sessions_of(ANOTHER.id),
    )

    instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert anothers_before == (
        instance.users.users[ANOTHER.id],
        instance.preferences.read(ANOTHER.id),
        instance.fakes.key_drafts.count_for_owner(ANOTHER.id),
        instance.sessions.sessions_of(ANOTHER.id),
    )
    assert "notes-id" not in instance.folders.forgotten


def test_a_dry_run_reports_the_real_runs_counts_and_changes_nothing() -> None:
    """S9 (#293): the dry run lists the real run's counts and changes nothing."""
    instance = an_instance()
    before = instance.state()

    dry = instance.people.erase_person(THE_PERSON.id, dry_run=True)

    assert instance.state() == before
    assert counts(dry) == _EVERY_KIND_OF_THE_PERSON
    real = instance.people.erase_person(THE_PERSON.id, dry_run=False)
    assert real == dry


def test_a_second_run_finds_nothing_left_to_remove() -> None:
    """people.md: a repeated erasure is idempotent and answers zero."""
    instance = an_instance()
    instance.people.erase_person(THE_PERSON.id, dry_run=False)

    again = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert counts(again) == _NOTHING


def test_an_unknown_subject_answers_zero_and_creates_nothing() -> None:
    """S9 (#293): an unknown subject answers zero counts and creates no row."""
    instance = an_instance()
    before = instance.state()

    dry = instance.people.erase_person("nobody-at-all", dry_run=True)
    real = instance.people.erase_person("nobody-at-all", dry_run=False)

    assert counts(dry) == _NOTHING
    assert counts(real) == _NOTHING
    assert instance.state() == before


def test_a_person_owning_no_source_is_erased_without_waiting_for_a_refresh() -> None:
    """S9 (#418 order): a person without a source has no lock to take."""
    instance = an_instance()
    sourceless = User(id="sourceless", username="sam", role=Role.USER)
    instance.users.users[sourceless.id] = sourceless

    with another_thread_holding(instance.decks.one_at_a_time):
        dry = instance.people.erase_person(sourceless.id, dry_run=True)
        real = instance.people.erase_person(sourceless.id, dry_run=False)

    assert counts(dry) == {**_NOTHING, PersonKind.ACCOUNT: 1}
    assert real == dry
    assert sourceless.id not in instance.users.users


def test_a_refresh_under_way_makes_a_real_run_answer_busy_and_delete_nothing() -> None:
    """S9 (#293): a refresh under way means busy and nothing deleted."""
    instance = an_instance()
    before = instance.state()

    with another_thread_holding(instance.decks.one_at_a_time):
        outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert outcome == PersonBusy(
        running=(KindCount(kind=PersonKind.RUNNING_WORK, count=1),),
    )
    assert instance.state() == before


def test_a_dry_run_lists_a_refresh_under_way_as_running_work() -> None:
    """S9 (#293): a dry run never waits and lists a running refresh as running work."""
    instance = an_instance()

    with another_thread_holding(instance.decks.one_at_a_time):
        outcome = instance.people.erase_person(THE_PERSON.id, dry_run=True)

    assert counts(outcome) == {
        **_EVERY_KIND_OF_THE_PERSON,
        PersonKind.RUNNING_WORK: 1,
    }


def test_an_unknown_subjects_dry_run_does_not_list_running_work() -> None:
    """S9 (#293): an unknown subject owns nothing, so no running work is listed."""
    instance = an_instance()

    with another_thread_holding(instance.decks.one_at_a_time):
        outcome = instance.people.erase_person("nobody-at-all", dry_run=True)

    assert counts(outcome) == _NOTHING


def _runs_of(instance: Instance, source_id: str) -> frozenset[BuildRun]:
    builder = instance.fakes.builder
    return frozenset(
        run
        for deck in instance.fakes.store.for_source(source_id)
        if deck.build is not None
        for path in (deck.build.directory, deck.build.pdf)
        if (run := builder.run_of(path)) is not None
    )


def _owned_source_ids(instance: Instance) -> set[str]:
    return {
        source.id
        for source in instance.decks.sources.all()
        if source.owner_id == THE_PERSON.id
    }


def test_every_directory_one_source_left_is_listed_once_by_its_kind() -> None:
    """E1-E3 (#483): a stuck mirror and two stuck builds are three entries."""
    stuck = FakeDeckFolders(stuck=frozenset({"talks-id"}))
    instance = an_instance(folders=stuck)
    talk_decks = (a_folder("talks-deck"), a_folder("talks-more"))
    stuck.carried["talks-id"] = talk_decks
    instance.decks.refresh()
    builder = instance.fakes.builder
    builder.stuck = _runs_of(instance, "talks-id")
    assert len(builder.stuck) == len(talk_decks)

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(outcome, PersonDiskLeftover)
    assert len(outcome.leftovers) == 1 + len(talk_decks)
    assert set(outcome.leftovers) == {
        LeftoverDirectory(kind=PersonKind.MIRRORS, path=stuck.mirror_of("talks-id")),
        *(
            LeftoverDirectory(kind=PersonKind.BUILDS, path=builder.directory_of(run))
            for run in builder.stuck
        ),
    }
    assert THE_PERSON.id in instance.users.users
    assert _owned_source_ids(instance) == {"talks-id"}
    assert len(instance.fakes.store.for_source("talks-id")) == len(talk_decks)


def test_every_stuck_source_is_listed_in_one_answer() -> None:
    """E4 (#483): two stuck sources are both listed; neither loses a row."""
    stuck = FakeDeckFolders(stuck=frozenset({"decks-id", "talks-id"}))
    instance = an_instance(folders=stuck)

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(outcome, PersonDiskLeftover)
    assert outcome.leftovers == tuple(
        LeftoverDirectory(kind=PersonKind.MIRRORS, path=stuck.mirror_of(source_id))
        for source_id in ("decks-id", "talks-id")
    )
    assert _owned_source_ids(instance) == {"decks-id", "talks-id"}
    assert instance.fakes.store.for_source("decks-id")
    assert instance.fakes.store.for_source("talks-id")


def test_a_stuck_source_does_not_keep_the_others_and_a_repeat_finishes() -> None:
    """E4 (#483): the removable source goes, the stuck one and the account stay."""
    stuck = FakeDeckFolders(stuck=frozenset({"talks-id"}))
    instance = an_instance(folders=stuck)

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(outcome, PersonDiskLeftover)
    assert _owned_source_ids(instance) == {"talks-id"}
    assert instance.fakes.store.for_source("decks-id") == ()
    assert "decks-id" not in stuck.carried
    assert instance.fakes.store.for_source("talks-id")
    assert THE_PERSON.id in instance.users.users
    stuck.stuck = frozenset()
    assert isinstance(
        instance.people.erase_person(THE_PERSON.id, dry_run=False), PersonErased
    )
    assert THE_PERSON.id not in instance.users.users
    assert counts(instance.people.erase_person(THE_PERSON.id, dry_run=True)) == _NOTHING


def test_a_leftover_counts_only_what_this_attempt_removed() -> None:
    """E5 (#483): the account and the stuck source count zero, the rest what went."""
    stuck = FakeDeckFolders(stuck=frozenset({"talks-id"}))
    instance = an_instance(folders=stuck)

    outcome = instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert isinstance(outcome, PersonDiskLeftover)
    assert {entry.kind: entry.count for entry in outcome.removed} == {
        PersonKind.ACCOUNT: 0,
        PersonKind.SESSIONS: 1,
        PersonKind.PREFERENCES: 1,
        PersonKind.KEY_DRAFT: 1,
        PersonKind.SOURCES: 1,
        PersonKind.DECKS: 1,
        PersonKind.RUNS: 1,
        PersonKind.MIRRORS: 1,
        PersonKind.BUILDS: 1,
    }


def test_a_build_no_run_owns_fails_loud_after_the_other_sources_went() -> None:
    """D2b (#483): the erasure goes on, keeps the account, then names the id."""
    instance = an_instance()
    store = instance.fakes.store
    (deck,) = store.for_source("decks-id")
    decks_runs = _runs_of(instance, "decks-id")
    talks_runs = _runs_of(instance, "talks-id")
    store.misplace_build(deck.slug, talk=Path("/nowhere/a-run/out/talk"))

    with pytest.raises(RuntimeError, match="decks-id") as raised:
        instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert "decks" not in str(raised.value).replace("decks-id", "")
    assert _owned_source_ids(instance) == {"decks-id"}
    assert store.for_source("decks-id")
    assert store.for_source("talks-id") == ()
    assert decks_runs <= instance.fakes.builder.on_disk
    assert talks_runs
    assert not talks_runs & instance.fakes.builder.on_disk
    assert THE_PERSON.id in instance.users.users


@dataclass
class _FoldersThatWatchTheRows(FakeDeckFolders):
    """Records what still stood in the database when a mirror was removed."""

    watch: Callable[[], tuple[object, ...]] = field(default=lambda: ())
    seen: list[tuple[object, ...]] = field(default_factory=list[tuple[object, ...]])

    def forget(self, source: Source) -> PurePosixPath | None:
        self.seen.append(self.watch())
        return super().forget(source)


def test_files_go_before_the_rows_that_name_them() -> None:
    """S9 (#293): files before rows."""
    folders = _FoldersThatWatchTheRows()
    instance = an_instance(folders=folders)
    folders.watch = lambda: (
        THE_PERSON.id in instance.users.users,
        len(instance.fakes.store.for_source("decks-id")),
        len(instance.decks.sources.all()),
    )

    instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert folders.seen[0] == (True, 1, 3)


@dataclass
class _RunsThatGrowWhenRead(FakeSourceRunStore):
    """A count that moves between the read that confirmed it and the removal."""

    def recent(self, source_id: str) -> tuple[SourceRun, ...]:
        found = super().recent(source_id)
        self.recorded.append(self.recorded[0])
        return found


def test_a_count_that_moves_under_the_hold_fails_loud_instead_of_skipping() -> None:
    """#418 order: a count that moves under the hold fails loud, never skips."""
    runs = _RunsThatGrowWhenRead()
    instance = an_instance(fakes=DecksFakes(source_runs=runs))

    with pytest.raises(RuntimeError, match="changed under the refresh lock"):
        instance.people.erase_person(THE_PERSON.id, dry_run=False)

    assert THE_PERSON.id in instance.users.users


@dataclass
class _SourcesThatVanishAfterTheFirstListing(FakeSourceStore):
    """A source removed by someone else between the listing and the count."""

    listings_left: int = 1000

    def all(self) -> tuple[Source, ...]:
        if self.listings_left == 0:
            return ()
        self.listings_left -= 1
        return super().all()


def test_a_source_that_vanishes_before_it_is_counted_fails_loud() -> None:
    """#418 order: a source that vanishes between listing and counting fails loud."""
    sources = _SourcesThatVanishAfterTheFirstListing()
    instance = an_instance(source_store=sources)
    sources.listings_left = 1

    with pytest.raises(RuntimeError, match="vanished while the person was counted"):
        instance.people.erase_person(THE_PERSON.id, dry_run=True)
