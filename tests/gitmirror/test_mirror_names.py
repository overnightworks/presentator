"""Folder names the mirror reads back exactly as they were committed."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from gitmirror.mirror import GitMirror
from gitmirror.model import Change, CredentialReference, GitSource
from tests.support.git_remote import MAIN_BRANCH, GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_PUSHED_LATER = datetime(2026, 1, 16, 9, tzinfo=UTC)


class _NoSecret:
    def resolve(self, reference: CredentialReference) -> str | None:
        return None


def a_mirror(url: str, *, directory: Path) -> GitMirror:
    return GitMirror(
        source=GitSource(url=url, ref=MAIN_BRANCH, credential=None),
        directory=directory / "mirror.git",
        credentials=_NoSecret(),
        pull_timeout=timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    "folder",
    [
        "ü",
        "Übersicht",
        "two words",
        'say "hi"',
        "back\\slash",
        "tab\there",
        "line\nbreak",
    ],
    ids=["umlaut", "capital-umlaut", "space", "quote", "backslash", "tab", "newline"],
)
def test_a_folder_is_listed_changed_and_read_under_its_real_name(
    folder: str,
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit(
        {f"{folder}/deck.toml": 'title = "T"\n', f"{folder}/slides.md": "# T\n"},
        at=_PUSHED_AT,
    )
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None

    assert {entry.name for entry in mirror.entries(revision)} == {folder}
    assert {entry.name for entry in mirror.entries(revision, inside=folder)} == {
        "deck.toml",
        "slides.md",
    }
    assert mirror.last_change(revision, folder) == Change(
        commit=revision.commit,
        at=_PUSHED_AT,
    )
    assert mirror.read(revision, f"{folder}/slides.md") == b"# T\n"


def test_a_folder_name_with_a_glob_character_is_not_read_as_a_pattern(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit({"a*/slides.md": "# Star\n"}, at=_PUSHED_AT)
    remote.commit({"ab/slides.md": "# Plain\n"}, at=_PUSHED_LATER)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None

    assert mirror.last_change(revision, "a*").at == _PUSHED_AT
    assert {entry.name for entry in mirror.entries(revision, inside="a*")} == {
        "slides.md",
    }
