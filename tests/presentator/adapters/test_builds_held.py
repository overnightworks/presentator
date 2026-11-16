"""A build's leavings are held and read only as the real files they were written as."""

import shutil
from pathlib import Path

import pytest

from presentator.adapters.builds import SlidevBuilds
from presentator.contracts.decks import OUTPUT_DIRECTORY, TALK_PAGE, Artefacts
from tests.presentator.adapters.support.build_support import (
    builds_under,
    lines_of,
    on_this_machine,
)
from tests.support.log_lines import json_logged

_TALK = "talk"
_PDF_FILE = "deck.pdf"
_A_DATABASE_FILE = "presentator.sqlite3"


_A_REAL_TALK_UNDER_THE_ROOT = Path("builds/a-run/real-out/talk")


@pytest.mark.parametrize(
    ("talk", "pdf"),
    [
        (Path("outside"), Path("outside/deck.pdf")),
        (Path("builds/through-a-link"), Path("builds/through-a-link/deck.pdf")),
        (Path("builds/a-run/out/talk"), Path("builds/a-run/out/talk/deck.pdf")),
        (Path("builds/../outside"), Path("builds/../outside/deck.pdf")),
        (_A_REAL_TALK_UNDER_THE_ROOT, Path("builds")),
    ],
    ids=[
        "a place outside the root",
        "a link out of the root",
        "a run whose out is a link inside the root",
        "a way up out of the root",
        "the root itself as the PDF",
    ],
)
def test_artefacts_that_do_not_stand_under_the_root_are_not_held(
    tmp_path: Path,
    talk: Path,
    pdf: Path,
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    builds.builds.mkdir(parents=True)
    outside = a_written_talk(tmp_path / "outside")
    (builds.builds / "through-a-link").symlink_to(outside)
    run = builds.builds / "a-run"
    a_written_talk(tmp_path / _A_REAL_TALK_UNDER_THE_ROOT)
    (run / OUTPUT_DIRECTORY).symlink_to("real-out")

    assert not builds.holds(Artefacts(directory=tmp_path / talk, pdf=tmp_path / pdf))


def a_written_talk(directory: Path) -> Path:
    """A talk's page and a PDF beside it, as real files in that directory."""
    directory.mkdir(parents=True)
    (directory / TALK_PAGE).write_text("a talk", encoding="utf-8")
    (directory / _PDF_FILE).write_bytes(b"%PDF-1.7")
    return directory


def test_a_file_of_a_build_is_read_as_it_was_written(tmp_path: Path) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)

    file = builds.read(run / _TALK / "assets" / "app.js")

    assert file is not None
    with file:
        assert file.read() == b"console.log(1)"


@pytest.mark.parametrize(
    ("replaced", "read"),
    [
        (_PDF_FILE, _PDF_FILE),
        ("talk/assets/app.js", "talk/assets/app.js"),
        ("talk/assets", "talk/assets/app.js"),
        ("talk", "talk/index.html"),
    ],
    ids=["the PDF", "an asset", "a directory on the way", "the talk itself"],
)
def test_a_file_replaced_by_a_link_after_the_build_was_held_is_not_read(
    tmp_path: Path,
    replaced: str,
    read: str,
) -> None:
    """The link comes after `holds` said yes, which is the gap `read` closes."""
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)
    assert builds.holds(Artefacts(directory=run / _TALK, pdf=run / _PDF_FILE))
    secret = a_secret_beside_the_root(tmp_path)
    target = run / replaced
    if target.is_dir():
        shutil.rmtree(target)
        target.symlink_to(secret.parent)
    else:
        target.unlink()
        target.symlink_to(secret)

    assert builds.read(run / read) is None


@pytest.mark.parametrize("nothing_there", ["no-such-file", "talk"])
def test_a_name_that_is_no_file_of_a_build_is_not_read(
    tmp_path: Path,
    nothing_there: str,
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)

    assert builds.read(run / nothing_there) is None


def test_a_path_that_leaves_the_root_is_not_read(tmp_path: Path) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    builds.builds.mkdir(parents=True)

    assert builds.read(a_secret_beside_the_root(tmp_path)) is None
    assert builds.read(builds.builds / ".." / _A_DATABASE_FILE) is None


def a_secret_beside_the_root(tmp_path: Path) -> Path:
    """Something this server keeps to itself, named like every file a build has."""
    secret = tmp_path / "secret" / _A_DATABASE_FILE
    secret.parent.mkdir(exist_ok=True)
    secret.write_bytes(b"what this server keeps to itself")
    (secret.parent / "app.js").write_bytes(secret.read_bytes())
    (secret.parent / TALK_PAGE).write_bytes(secret.read_bytes())
    return secret


def a_run_with_an_asset(builds: SlidevBuilds) -> Path:
    """A run holding a talk with one asset, and its PDF, as real files."""
    run = builds.builds / "a-run"
    talk = a_written_talk(run / _TALK)
    (run / _PDF_FILE).write_bytes((talk / _PDF_FILE).read_bytes())
    (talk / _PDF_FILE).unlink()
    (talk / "assets").mkdir()
    (talk / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return run


def test_artefacts_that_were_never_written_are_not_held(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    never_written = builds.builds / "a-run-that-never-was" / _TALK

    with json_logged(capsys) as logged:
        held = builds.holds(
            Artefacts(directory=never_written, pdf=never_written / "deck.pdf"),
        )

    assert not held
    (line,) = lines_of(logged, "build_artefacts.refused")
    assert line["reason"] == "not_as_written"
    assert "a-run-that-never-was" not in logged.text
