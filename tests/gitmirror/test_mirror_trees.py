"""The mirror's tree read against crafted tree objects in a real bare repository."""

import shutil
from pathlib import Path
from typing import Literal

import pytest

from gitmirror.model import MirrorError
from tests.support.git_remote import GitRemote, with_the_program
from tests.support.mirror_support import a_mirror


@pytest.mark.parametrize("entry", ["file", "tree"])
@pytest.mark.parametrize("deeper", [False, True])
@pytest.mark.parametrize("name", [".", "..", ".git"])
def test_a_tree_entry_that_names_a_directory_of_its_own_ends_the_read_before_any_write(
    remote: GitRemote,
    tmp_path: Path,
    name: str,
    *,
    deeper: bool,
    entry: Literal["file", "tree"],
) -> None:
    remote.push_crafted_deck(name, entry=entry, deeper=deeper)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    work = tmp_path / "work"

    with pytest.raises(MirrorError):
        mirror.export(revision, "deck", into=work / "deck")

    assert not work.exists()
    assert not (tmp_path / "payload.txt").exists()


@pytest.mark.parametrize("name", [".github", ".env", "..hidden", "..."])
def test_a_tree_whose_names_merely_start_with_a_dot_is_written_out_as_it_stands(
    remote: GitRemote,
    tmp_path: Path,
    name: str,
) -> None:
    remote.push_crafted_deck(name, entry="tree")
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
    remote.push_crafted_deck("sub", entry="tree")
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    real_git = shutil.which("git")
    assert real_git is not None
    with_the_program(
        monkeypatch,
        tmp_path,
        "git",
        "#!/bin/sh\n"
        'for argument in "$@"; do\n'
        '  if [ "$argument" = ls-tree ]; then\n'
        "    printf '100644 blob 0000000000000000000000000000000000000000\\t\\0'\n"
        "    exit 0\n"
        "  fi\n"
        "done\n"
        f'exec {real_git} "$@"\n',
    )

    with pytest.raises(MirrorError):
        mirror.entries(revision, inside="deck")
