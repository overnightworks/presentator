"""The mirror's tree read against crafted tree objects in a real bare repository."""

import os
import subprocess
from pathlib import Path

import pytest

from gitmirror.model import MirrorError
from tests.support.git_remote import MAIN_BRANCH, GitRemote
from tests.support.mirror_support import a_mirror


def _plant_tree(remote: GitRemote, entries: list[tuple[str, str, str]]) -> str:
    """Write one tree object into the remote, whatever names it carries.

    `git mktree` refuses the names a crafted tree needs, so the raw object is
    written by hand: each entry is a mode, a name and the hex id it points at.
    """

    def write(object_type: str, content: bytes) -> str:
        written = subprocess.run(  # noqa: S603 - fixed git argv in a test
            ["git", "hash-object", "-w", "--literally", "-t", object_type, "--stdin"],  # noqa: S607 - git from PATH
            capture_output=True,
            check=True,
            cwd=remote.bare,
            input=content,
        )
        return written.stdout.decode().strip()

    raw = b"".join(
        f"{mode} {name}".encode() + b"\0" + bytes.fromhex(target)
        for mode, name, target in entries
    )
    return write("tree", raw)


def _push_crafted_deck(remote: GitRemote, name: str, *, deeper: bool = False) -> None:
    blob = (
        subprocess.run(
            ["git", "hash-object", "-w", "--stdin"],  # noqa: S607 - git from PATH
            capture_output=True,
            check=True,
            cwd=remote.bare,
            input=b"payload\n",
        )
        .stdout.decode()
        .strip()
    )
    below = _plant_tree(remote, [("100644", "payload.txt", blob)])
    if deeper:
        sub = _plant_tree(remote, [("40000", name, below)])
        deck = _plant_tree(remote, [("100644", "a.txt", blob), ("40000", "sub", sub)])
    else:
        deck = _plant_tree(remote, [("40000", name, below)])
    root = _plant_tree(remote, [("40000", "deck", deck)])
    commit = (
        subprocess.run(  # noqa: S603 - fixed git argv in a test
            ["git", "commit-tree", root, "-m", "a push"],  # noqa: S607 - git from PATH
            capture_output=True,
            check=True,
            cwd=remote.bare,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "a test",
                "GIT_AUTHOR_EMAIL": "test@example.invalid",
                "GIT_COMMITTER_NAME": "a test",
                "GIT_COMMITTER_EMAIL": "test@example.invalid",
            },
        )
        .stdout.decode()
        .strip()
    )
    subprocess.run(  # noqa: S603 - fixed git argv in a test
        ["git", "update-ref", f"refs/heads/{MAIN_BRANCH}", commit],  # noqa: S607 - git from PATH
        check=True,
        cwd=remote.bare,
    )


@pytest.mark.parametrize("deeper", [False, True])
@pytest.mark.parametrize("name", [".", "..", ".git"])
def test_a_tree_entry_that_names_a_directory_of_its_own_ends_the_read_before_any_write(
    remote: GitRemote,
    tmp_path: Path,
    name: str,
    *,
    deeper: bool,
) -> None:
    _push_crafted_deck(remote, name, deeper=deeper)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    work = tmp_path / "work"

    with pytest.raises(MirrorError):
        mirror.export(revision, "deck", into=work / "deck")

    assert not work.exists()
    assert not (tmp_path / "payload.txt").exists()


@pytest.mark.parametrize("name", [".github", ".env", "..hidden", "a.b"])
def test_a_tree_whose_names_merely_start_with_a_dot_is_written_out_as_it_stands(
    remote: GitRemote,
    tmp_path: Path,
    name: str,
) -> None:
    _push_crafted_deck(remote, name)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    written = tmp_path / "work" / "deck"

    mirror.export(revision, "deck", into=written)

    assert (written / name / "payload.txt").read_bytes() == b"payload\n"


def test_a_listing_record_with_an_empty_name_is_rejected(
    remote: GitRemote,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _push_crafted_deck(remote, "sub")
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    listing = b"100644 blob " + b"0" * 40 + b"\t\0"
    monkeypatch.setattr(type(mirror), "_run", lambda *_a, **_k: listing)

    with pytest.raises(MirrorError):
        mirror.entries(revision, inside="deck")
