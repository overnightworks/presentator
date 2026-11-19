"""The runs on a process's disk: kept in the store, fetched from it, bounded."""

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from presentator.adapters.build_cache import BuildCache, RunObjects
from presentator.adapters.build_runs import ARRIVING_PREFIX
from presentator.application import build_retention
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    MANIFEST_FILE,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    SLIDES_FILE,
    TALK_DIRECTORY,
    BuildRun,
)
from tests.presentator.adapters.support.build_support import (
    builds_under,
    on_this_machine,
)
from tests.presentator.application.support.fakes import FakeDeckStore, FakeSourceStore
from tests.support.log_lines import json_logged
from tests.support.object_store import ObjectStore
from tests.support.run_objects import A_CACHE_BOUND, DirectoryRunObjects

_PAGE = "index.html"
_A_RUNS_BYTES = 4096


def a_cache(root: Path, store: RunObjects, bound: int = A_CACHE_BOUND) -> BuildCache:
    return BuildCache(root=root, store=store, bound_bytes=bound)


def built_run(cache: BuildCache, name: str) -> Path:
    """A run as a build leaves it: its talk, its PDF and the deck's text."""
    run = cache.root / name
    talk = run / OUTPUT_DIRECTORY / TALK_DIRECTORY
    talk.mkdir(parents=True)
    (talk / _PAGE).write_text(f"the talk of {name}", encoding="utf-8")
    (run / OUTPUT_DIRECTORY / PDF_FILE).write_bytes(b"x" * _A_RUNS_BYTES)
    deck = run / DECK_DIRECTORY
    deck.mkdir()
    (deck / SLIDES_FILE).write_text("# One\n", encoding="utf-8")
    (deck / MANIFEST_FILE).write_text('title = "The Talk"\n', encoding="utf-8")
    return run


def on_disk(cache: BuildCache) -> set[str]:
    return {entry.name for entry in cache.root.iterdir()}


@pytest.fixture
def store(tmp_path: Path) -> DirectoryRunObjects:
    return DirectoryRunObjects(tmp_path / "store")


def test_a_run_one_process_kept_is_served_by_another_from_the_store(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    built_run(building, "run-1")
    assert building.keep("run-1")

    served = a_cache(tmp_path / "b", store).local("run-1")

    assert served is not None
    assert served == tmp_path / "b" / "run-1"
    talk = served / OUTPUT_DIRECTORY / TALK_DIRECTORY / _PAGE
    assert talk.read_text(encoding="utf-8") == "the talk of run-1"
    assert (served / OUTPUT_DIRECTORY / PDF_FILE).stat().st_size == _A_RUNS_BYTES
    assert (served / DECK_DIRECTORY / SLIDES_FILE).is_file()
    assert (served / DECK_DIRECTORY / MANIFEST_FILE).is_file()


def test_a_link_in_the_talk_does_not_travel(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    run = built_run(building, "run-1")
    secret = tmp_path / "secret"
    secret.write_text("not the talk's", encoding="utf-8")
    (run / OUTPUT_DIRECTORY / TALK_DIRECTORY / "linked").symlink_to(secret)
    assert building.keep("run-1")

    served = a_cache(tmp_path / "b", store).local("run-1")

    assert served is not None
    assert not (served / OUTPUT_DIRECTORY / TALK_DIRECTORY / "linked").exists()


def test_a_store_that_does_not_answer_serves_what_the_disk_holds_and_nothing_else(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    built_run(building, "run-1")
    assert building.keep("run-1")
    serving = a_cache(tmp_path / "b", store)
    assert serving.local("run-1") is not None
    store.answering = False

    assert serving.local("run-1") == tmp_path / "b" / "run-1"
    assert a_cache(tmp_path / "c", store).local("run-1") is None


def test_a_run_the_store_did_not_take_is_not_kept(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    store.answering = False
    building = a_cache(tmp_path / "a", store)
    built_run(building, "run-1")

    assert not building.keep("run-1")
    store.answering = True
    assert a_cache(tmp_path / "b", store).local("run-1") is None


def test_removing_a_run_takes_it_out_of_the_store_and_off_this_disk(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    built_run(building, "run-1")
    assert building.keep("run-1")

    assert building.remove("run-1")

    assert on_disk(building) == set()
    assert store.names() == frozenset()
    assert a_cache(tmp_path / "b", store).local("run-1") is None


def test_a_process_turned_away_drops_the_copies_no_row_names_and_keeps_the_store(
    tmp_path: Path,
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    for name in ("named", "unnamed"):
        built_run(builds.cache, name)
        assert builds.cache.keep(name)

    builds.drop_copies_except(frozenset({BuildRun("named")}))

    assert on_disk(builds.cache) == {"named"}
    assert builds.cache.store.names() == frozenset({"named", "unnamed"})


def test_a_process_that_never_made_its_root_drops_nothing_and_warns_of_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))

    with json_logged(capsys) as logged:
        builds.drop_copies_except(frozenset())

    assert not builds.cache.root.exists()
    assert [one["level"] for one in logged.lines] == []


def test_a_sweep_over_a_root_never_made_takes_the_runs_no_row_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    built_run(builds.cache, "unnamed")
    assert builds.cache.keep("unnamed")
    shutil.rmtree(builds.cache.root)

    with json_logged(capsys) as logged:
        build_retention.sweep(builds, FakeSourceStore(), FakeDeckStore())

    assert builds.cache.store.names() == frozenset()
    assert [line["event"] for line in logged.lines] == ["build_sweep.completed"]


@dataclass
class _DroppedWhileFetching(DirectoryRunObjects):
    """A store whose every fetch meets another thread's drop of unnamed copies."""

    meanwhile: Callable[[], None] = field(default=lambda: None)

    def fetch(self, run: str, *, archive: Path, pdf: Path) -> bool:
        self.meanwhile()
        return super().fetch(run, archive=archive, pdf=pdf)


def test_a_fetch_under_way_is_no_copy_a_drop_beside_it_takes(tmp_path: Path) -> None:
    store = _DroppedWhileFetching(tmp_path / "store")
    building = a_cache(tmp_path / "a", store)
    built_run(building, "run-1")
    assert building.keep("run-1")
    serving = a_cache(tmp_path / "b", store)
    store.meanwhile = lambda: serving.drop_copies_except(frozenset({"run-1"}))

    served = serving.local("run-1")

    assert served is not None
    talk = served / OUTPUT_DIRECTORY / TALK_DIRECTORY / _PAGE
    assert talk.read_text(encoding="utf-8") == "the talk of run-1"


def test_dropping_unfinished_fetches_takes_every_staged_copy_and_keeps_the_runs(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    cache = a_cache(tmp_path / "builds", store)
    built_run(cache, "run-1")
    left_behind = cache.root / f"{ARRIVING_PREFIX}x" / OUTPUT_DIRECTORY
    left_behind.mkdir(parents=True)
    (left_behind / PDF_FILE).write_bytes(b"x" * _A_RUNS_BYTES)

    cache.drop_unfinished_fetches()

    assert on_disk(cache) == {"run-1"}


def test_a_run_under_way_counts_but_is_never_taken_by_the_bound(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    cache = a_cache(tmp_path / "a", store, bound=_A_RUNS_BYTES)
    built_run(cache, "under-way")
    built_run(cache, "run-1")

    assert cache.keep("run-1")

    assert on_disk(cache) == {"under-way", "run-1"}


def test_a_process_that_builds_past_its_bound_lets_its_older_kept_run_go(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    cache = a_cache(tmp_path / "a", store, bound=2 * _A_RUNS_BYTES)
    built_run(cache, "run-1")
    assert cache.keep("run-1")
    built_run(cache, "run-2")

    assert cache.keep("run-2")

    assert on_disk(cache) == {"run-2"}
    assert cache.local("run-1") is not None


def test_past_its_bound_the_least_recently_served_run_goes_and_is_fetched_again(
    tmp_path: Path, object_store: ObjectStore
) -> None:
    building = a_cache(tmp_path / "a", object_store.runs)
    for name in ("run-1", "run-2"):
        built_run(building, name)
        assert building.keep(name)
    # Room for one run's PDF and talk, never for two.
    serving = a_cache(tmp_path / "b", object_store.runs, bound=2 * _A_RUNS_BYTES)

    assert serving.local("run-1") is not None
    assert serving.local("run-2") is not None
    assert on_disk(serving) == {"run-2"}

    again = serving.local("run-1")

    assert again is not None
    talk = again / OUTPUT_DIRECTORY / TALK_DIRECTORY / _PAGE
    assert talk.read_text(encoding="utf-8") == "the talk of run-1"
    assert on_disk(serving) == {"run-1"}


def test_a_run_that_cannot_be_packed_is_not_kept(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    run = built_run(building, "run-1")
    run.chmod(0o500)
    try:
        assert not building.keep("run-1")
    finally:
        run.chmod(0o700)

    assert store.names() == frozenset()


def test_a_run_whose_archive_does_not_unpack_is_not_served_and_leaves_nothing(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "talk.tar").write_bytes(b"no archive at all")
    (broken / PDF_FILE).write_bytes(b"%PDF")
    assert store.put("run-1", archive=broken / "talk.tar", pdf=broken / PDF_FILE)
    serving = a_cache(tmp_path / "b", store)

    assert serving.local("run-1") is None
    assert on_disk(serving) == set()


def test_a_kept_run_stays_kept_where_this_disk_cannot_be_listed(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    cache = a_cache(tmp_path / "a", store, bound=_A_RUNS_BYTES)
    built_run(cache, "run-1")
    assert cache.keep("run-1")
    built_run(cache, "run-2")
    cache.root.chmod(0o300)
    try:
        assert cache.keep("run-2")
    finally:
        cache.root.chmod(0o700)

    assert on_disk(cache) == {"run-1", "run-2"}
    assert store.names() == frozenset({"run-1", "run-2"})


def test_a_run_the_bound_could_not_take_off_disk_frees_nothing_and_the_next_one_goes(
    tmp_path: Path, store: DirectoryRunObjects, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = a_cache(tmp_path / "a", store, bound=3 * _A_RUNS_BYTES)
    for served_at, name in enumerate(("run-1", "run-2"), start=1):
        run = built_run(cache, name)
        assert cache.keep(name)
        os.utime(run / OUTPUT_DIRECTORY, (served_at, served_at))
    built_run(cache, "run-3")
    removing = shutil.rmtree

    def stuck_on_the_oldest(path: Path, *, ignore_errors: bool = False) -> None:
        if path.name != "run-1":
            removing(path, ignore_errors=ignore_errors)

    monkeypatch.setattr(shutil, "rmtree", stuck_on_the_oldest)

    assert cache.keep("run-3")

    assert on_disk(cache) == {"run-1", "run-3"}


def test_a_run_a_removal_left_empty_is_taken_by_the_bound_with_the_rest(
    tmp_path: Path, store: DirectoryRunObjects
) -> None:
    building = a_cache(tmp_path / "a", store)
    for name in ("run-1", "run-2"):
        built_run(building, name)
        assert building.keep(name)
    serving = a_cache(tmp_path / "b", store, bound=2 * _A_RUNS_BYTES)
    assert serving.local("run-2") is not None
    # What a removal leaves that took the run's contents but not its directory.
    (serving.root / "run-1").mkdir()
    built_run(serving, "run-3")
    assert serving.keep("run-3")

    assert on_disk(serving) == {"run-3"}
