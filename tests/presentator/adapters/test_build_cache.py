"""The runs on a process's disk: kept in the store, fetched from it, bounded."""

from pathlib import Path

import pytest

from presentator.adapters.build_cache import BuildCache, RunObjects
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    MANIFEST_FILE,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    SLIDES_FILE,
    TALK_DIRECTORY,
)
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
