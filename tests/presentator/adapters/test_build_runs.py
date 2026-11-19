"""The runs of the build on disk, against a real temporary root."""

import shutil
import stat
from pathlib import Path

import pytest

from presentator.adapters.build_runs import (
    listed_runs,
    removed_run,
    taken_away,
)
from presentator.contracts.decks import BuildRun
from tests.presentator.adapters.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
)
from tests.presentator.adapters.support.person_postgres_support import (
    PostgresInstance,
    an_instance_with_both_people_on_postgres,
)
from tests.support.log_lines import json_logged


def a_run(root: Path, name: str) -> Path:
    """A real run: its `out` directory holds a talk and a PDF."""
    out = root / name / "out"
    (out / "talk").mkdir(parents=True)
    (out / "talk" / "index.html").write_text("a talk", encoding="utf-8")
    (out / "deck.pdf").write_bytes(b"%PDF")
    return out


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "builds"
    directory.mkdir()
    return directory


def test_only_real_runs_are_listed_and_everything_else_is_left_alone(
    root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#505 matrix: foreign file and directory in the root, symlinked run."""
    a_run(root, "real")
    (root / "a-file").write_text("x", encoding="utf-8")
    (root / "no-output").mkdir()
    (root / "no-output" / "keep").write_text("x", encoding="utf-8")
    (root / "linked-run").symlink_to(tmp_path)
    (root / "linked-out").mkdir()
    (root / "linked-out" / "out").symlink_to(tmp_path)

    with json_logged(capsys) as logged:
        assert listed_runs(root) == {"real"}
    (strays,) = logged.lines
    assert (strays["event"], strays["stray_count"]) == ("build_root_strays.found", 4)
    assert "no-output" not in logged.text
    assert sorted(path.name for path in root.iterdir()) == [
        "a-file",
        "linked-out",
        "linked-run",
        "no-output",
        "real",
    ]


def test_an_empty_directory_under_the_root_is_a_run_that_is_listed_and_removed(
    root: Path,
) -> None:
    """An empty directory holds nothing to lose and is a run's last state."""
    (root / "empty").mkdir()

    assert listed_runs(root) == {"empty"}
    assert removed_run(root, "empty")

    assert not (root / "empty").exists()


def test_a_removal_that_stopped_halfway_is_finished_by_the_next_attempt(
    root: Path,
) -> None:
    """A root closed to writing lets a run be emptied but not taken away."""
    a_run(root, "halfway")
    root.chmod(0o500)
    try:
        assert not removed_run(root, "halfway")
    finally:
        root.chmod(0o700)

    assert list((root / "halfway").iterdir()) == []
    assert listed_runs(root) == {"halfway"}
    assert removed_run(root, "halfway")
    assert not (root / "halfway").exists()


def test_a_root_that_cannot_be_listed_lists_nothing_at_all(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Root unlisted."""
    a_file = tmp_path / "a-file"
    a_file.write_text("no directory", encoding="utf-8")

    with json_logged(capsys) as logged:
        assert listed_runs(a_file) is None

    (line,) = logged.lines
    assert (line["event"], line["reason"], line["error_type"]) == (
        "build_root_listing.refused",
        "unlistable",
        "NotADirectoryError",
    )
    assert "a-file" not in logged.text


def test_a_root_never_made_lists_no_run_and_refuses_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Root missing."""
    with json_logged(capsys) as logged:
        assert listed_runs(tmp_path / "missing") == frozenset()

    assert logged.lines == []


def test_removing_a_run_takes_its_whole_directory_even_where_a_deck_closed_it(
    root: Path,
) -> None:
    """#505 matrix: stuck (mode-closed) run."""
    out = a_run(root, "stuck")
    (out / "talk" / "closed").mkdir()
    (out / "talk" / "closed" / "file").write_text("x", encoding="utf-8")
    (out / "talk" / "closed").chmod(0o000)

    assert removed_run(root, "stuck")

    assert not (root / "stuck").exists()


def test_a_removal_that_stops_halfway_leaves_no_directory_closed_to_others(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#529 sentence 4: a removal that stops halfway leaves nothing closed to others."""
    out = a_run(root, "halfway")
    (out / "talk" / "closed").mkdir()
    (out / "talk" / "closed").chmod(0o000)

    def stop_halfway(*_arguments: object, **_keywords: object) -> None:
        """Remove nothing."""

    monkeypatch.setattr(shutil, "rmtree", stop_halfway)

    assert not removed_run(root, "halfway")

    for directory in (root / "halfway", out, out / "talk", out / "talk" / "closed"):
        assert directory.stat().st_mode & stat.S_IRWXO == stat.S_IROTH | stat.S_IXOTH


def test_a_run_that_cannot_be_looked_at_is_not_reported_gone(
    root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """#505 invariant: a removal that could not be confirmed is not believed."""
    a_run(root, "unseen")
    root.chmod(0o600)
    try:
        with json_logged(capsys) as logged:
            assert not taken_away(root / "unseen")
    finally:
        root.chmod(0o700)

    (line,) = logged.lines
    assert (line["event"], line["reason"], line["run_id"]) == (
        "build_run_removal.refused",
        "stayed",
        "unseen",
    )


def test_a_directory_that_cannot_be_read_is_not_taken_for_an_empty_run(
    root: Path,
) -> None:
    """An unreadable directory is not known to hold nothing."""
    (root / "closed").mkdir()
    (root / "closed").chmod(0o000)
    try:
        assert listed_runs(root) == frozenset()
        assert not removed_run(root, "closed")
    finally:
        (root / "closed").chmod(0o700)

    assert (root / "closed").is_dir()


def test_removing_a_run_that_is_already_gone_succeeds(root: Path) -> None:
    """#505 invariant: remove is idempotent."""
    assert removed_run(root, "never-there")


def test_removing_leaves_the_other_runs_alone(root: Path) -> None:
    """#505 invariant: remove touches one run only."""
    a_run(root, "one")
    a_run(root, "other")

    assert removed_run(root, "one")

    assert listed_runs(root) == {"other"}


def test_removing_refuses_a_directory_that_is_not_shaped_like_a_run(
    root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """#505 invariant: remove refuses a foreign directory."""
    (root / "foreign").mkdir()
    (root / "foreign" / "keep").write_text("x", encoding="utf-8")

    with json_logged(capsys) as logged:
        assert not removed_run(root, "foreign")

    assert (root / "foreign" / "keep").exists()
    (line,) = logged.lines
    assert (line["event"], line["reason"]) == ("build_run_removal.refused", "not_a_run")
    assert "foreign" not in logged.text


def test_removing_a_link_to_a_run_leaves_the_directory_it_points_at(
    root: Path, tmp_path: Path
) -> None:
    """#505 matrix: symlinked run and symlinked out."""
    elsewhere = tmp_path / "elsewhere"
    a_run(tmp_path, "elsewhere")
    (root / "linked").symlink_to(elsewhere)

    assert not removed_run(root, "linked")

    assert (elsewhere / "out" / "deck.pdf").exists()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "/abs"])
def test_a_run_names_exactly_one_directory_under_the_root(name: str) -> None:
    """#505 invariant: remove is anchored to the configured root."""
    with pytest.raises(ValueError, match="one name"):
        BuildRun(name)


def test_a_refresh_over_a_real_root_keeps_what_rows_deliver_and_takes_the_rest(
    tmp_path: Path, database_url: str
) -> None:
    """#505 matrix: one end-to-end refresh over a real root."""
    with an_instance_with_both_people_on_postgres(tmp_path, database_url) as instance:
        _refresh_twice_over_a_leftover(instance)


def _refresh_twice_over_a_leftover(instance: PostgresInstance) -> None:
    leftover = a_run(instance.builds, "crashed")
    (instance.builds / "foreign-file").write_text("x", encoding="utf-8")

    instance.decks.refresh()
    instance.decks.refresh()

    assert not leftover.parent.exists()
    assert (instance.builds / "foreign-file").exists()
    for person in (THE_PERSON, ANOTHER):
        assert instance.build_of(person).exists()
