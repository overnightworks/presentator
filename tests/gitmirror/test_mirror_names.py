"""Folder names the mirror reads back exactly as they were committed."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitmirror.model import Change
from tests.support.git_remote import GitRemote
from tests.support.mirror_support import a_mirror

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_PUSHED_LATER = datetime(2026, 1, 16, 9, tzinfo=UTC)


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
    assert mirror.read(revision, f"{folder}/deck.toml") == b'title = "T"\n'


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


def test_a_file_name_that_is_not_utf8_is_listed_and_read_back_byte_for_byte(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    name = os.fsdecode(b"bild\xe4.png")
    remote.commit({f"deck/{name}": "pixels\n", "deck/deck.toml": "t\n"}, at=_PUSHED_AT)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None

    assert {entry.name for entry in mirror.entries(revision, inside="deck")} == {
        name,
        "deck.toml",
    }
    assert mirror.read(revision, f"deck/{name}") == b"pixels\n"
    assert mirror.last_change(revision, f"deck/{name}").at == _PUSHED_AT


def test_a_folder_name_starting_with_a_dash_is_not_read_as_an_option(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit({"-deck/slides.md": "# Dash\n"}, at=_PUSHED_AT)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None

    assert {entry.name for entry in mirror.entries(revision, inside="-deck")} == {
        "slides.md",
    }
    assert mirror.last_change(revision, "-deck").at == _PUSHED_AT
