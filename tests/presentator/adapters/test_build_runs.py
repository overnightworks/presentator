"""The runs of the build on disk, against a real temporary root."""

import logging
import shutil
import stat
from pathlib import Path

import pytest

from presentator.adapters.build_runs import (
    listed_runs,
    removed_run,
    run_name_of,
    taken_away,
)
from presentator.ports.decks import BuildRun
from tests.presentator.adapters.support.person_instance_support import (
    ANOTHER,
    THE_PERSON,
    an_instance_with_both_people,
)


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


@pytest.mark.parametrize("stored", ["talk", "deck.pdf"])
def test_the_talk_and_the_pdf_of_a_run_both_belong_to_it(
    root: Path, stored: str
) -> None:
    """#505 invariant: a run owns its talk and its PDF."""
    assert run_name_of(root, root / "abc" / "out" / stored) == "abc"


@pytest.mark.parametrize(
    "stored",
    [
        "abc/talk",
        "abc/out/other",
        "abc/outx/talk",
        "abc/out/talk/deeper",
        "abc/def/out/talk",
        "../elsewhere/out/talk",
        "abc/../out/talk",
        "abc/commit/talk",
    ],
    ids=[
        "no-output-directory",
        "other-file",
        "other-output-name",
        "below-the-talk",
        "two-levels",
        "parent-name-escapes-the-root",
        "dotdot-run-name",
        "the-old-shape",
    ],
)
def test_a_path_of_any_other_shape_belongs_to_no_run(root: Path, stored: str) -> None:
    """#505 matrix: wrongly shaped path inside the root."""
    assert run_name_of(root, root / stored) is None


def test_a_path_outside_the_root_belongs_to_no_run(root: Path, tmp_path: Path) -> None:
    """#505 matrix: row outside the root."""
    assert run_name_of(root, tmp_path / "other" / "abc" / "out" / "talk") is None


def test_only_real_runs_are_listed_and_everything_else_is_left_alone(
    root: Path, tmp_path: Path
) -> None:
    """#505 matrix: foreign file and directory in the root, symlinked run."""
    a_run(root, "real")
    (root / "a-file").write_text("x", encoding="utf-8")
    (root / "no-output").mkdir()
    (root / "no-output" / "keep").write_text("x", encoding="utf-8")
    (root / "linked-run").symlink_to(tmp_path)
    (root / "linked-out").mkdir()
    (root / "linked-out" / "out").symlink_to(tmp_path)

    assert listed_runs(root) == {"real"}
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
    """#705: an empty directory holds nothing to lose and is a run's last state."""
    (root / "empty").mkdir()

    assert listed_runs(root) == {"empty"}
    assert removed_run(root, "empty")

    assert not (root / "empty").exists()


def test_a_removal_that_stopped_halfway_is_finished_by_the_next_attempt(
    root: Path,
) -> None:
    """#705: a root closed to writing lets a run be emptied but not taken away."""
    a_run(root, "halfway")
    root.chmod(0o500)
    try:
        assert not removed_run(root, "halfway")
    finally:
        root.chmod(0o700)

    assert listed_runs(root) == {"halfway"}
    assert removed_run(root, "halfway")
    assert not (root / "halfway").exists()


def test_a_root_that_cannot_be_listed_lists_nothing_at_all(tmp_path: Path) -> None:
    """#505 matrix: root unlisted or missing."""
    assert listed_runs(tmp_path / "missing") is None


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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#505 invariant: a removal that could not be confirmed is not believed."""
    a_run(root, "unseen")
    root.chmod(0o600)
    try:
        with caplog.at_level(logging.WARNING):
            assert not taken_away(root / "unseen")
    finally:
        root.chmod(0o700)

    assert "unseen" in caplog.text


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
    root: Path,
) -> None:
    """#505 invariant: remove refuses a foreign directory."""
    (root / "foreign").mkdir()
    (root / "foreign" / "keep").write_text("x", encoding="utf-8")

    assert not removed_run(root, "foreign")

    assert (root / "foreign" / "keep").exists()


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
    tmp_path: Path,
) -> None:
    """#505 matrix: one end-to-end refresh over a real root."""
    instance = an_instance_with_both_people(tmp_path)
    leftover = a_run(instance.builds, "crashed")
    (instance.builds / "foreign-file").write_text("x", encoding="utf-8")

    instance.decks.refresh()
    instance.decks.refresh()

    assert not leftover.parent.exists()
    assert (instance.builds / "foreign-file").exists()
    for person in (THE_PERSON, ANOTHER):
        assert instance.build_of(person).exists()
