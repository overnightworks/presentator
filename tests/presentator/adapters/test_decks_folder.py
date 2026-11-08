"""Deck folders under a source's own folder, read out of a real repository."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.contracts.decks import DeckFolder, Source
from tests.presentator.adapters.support.key_draft_support import a_key_drafts_store
from tests.support.deck_source_support import (
    OWNER,
    a_source,
    a_source_database,
    a_source_store,
    a_source_write,
    folders_under,
)
from tests.support.git_remote import GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_LATER = datetime(2026, 1, 16, 9, tzinfo=UTC)


def folders_read(tmp_path: Path, source: Source) -> tuple[DeckFolder, ...]:
    poll = folders_under(tmp_path).folders(source)
    assert poll.folders is not None
    return poll.folders


def names_read(tmp_path: Path, source: Source) -> set[str]:
    return {folder.name for folder in folders_read(tmp_path, source)}


# DK18
def test_only_the_folders_directly_under_the_sources_folder_are_decks(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.commit(
        {
            "examples/a/slides.md": "# A\n",
            "examples/b/slides.md": "# B\n",
            "c/slides.md": "# C\n",
            "examples/loose.md": "not a deck\n",
        },
        at=_PUSHED_AT,
    )

    assert names_read(tmp_path, a_source(remote.url, folder="examples")) == {"a", "b"}


def test_a_two_level_folder_resolves(remote: GitRemote, tmp_path: Path) -> None:
    remote.commit(
        {"talks/2026/a/slides.md": "# A\n", "talks/b/slides.md": "# B\n"}, at=_PUSHED_AT
    )

    assert names_read(tmp_path, a_source(remote.url, folder="talks/2026")) == {"a"}


def test_a_nested_decks_title_and_files_are_read_from_its_manifest(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.commit(
        {
            "examples/a/slides.md": "# A\n",
            "examples/a/deck.toml": 'title = "The Nested Talk"\n',
        },
        at=_PUSHED_AT,
    )

    (read,) = folders_read(tmp_path, a_source(remote.url, folder="examples"))

    assert read.title == "The Nested Talk"
    assert {"slides.md", "deck.toml"} <= read.file_names


def test_a_push_outside_the_folder_leaves_a_nested_decks_commit_alone(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.commit({"examples/a/slides.md": "# A\n"}, at=_PUSHED_AT)
    first = remote.head
    remote.commit({"elsewhere/slides.md": "# E\n"}, at=_LATER)

    (read,) = folders_read(tmp_path, a_source(remote.url, folder="examples"))

    assert (read.commit, read.changed_at) == (first, _PUSHED_AT)


@pytest.mark.parametrize("folder", ["missing", "a-file"])
def test_a_folder_that_is_missing_or_a_file_carries_no_deck(
    remote: GitRemote, tmp_path: Path, folder: str
) -> None:
    remote.commit(
        {"a-file": "just a file\n", "other/slides.md": "# O\n"}, at=_PUSHED_AT
    )

    assert names_read(tmp_path, a_source(remote.url, folder=folder)) == set()


def test_a_symlink_as_the_folder_is_not_followed(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.commit({"real/a/slides.md": "# A\n"}, at=_PUSHED_AT)
    remote.commit_symlink(path="examples", target="real", at=_LATER)

    assert names_read(tmp_path, a_source(remote.url, folder="examples")) == set()


def test_a_symlink_as_a_deck_folder_is_no_deck(
    remote: GitRemote, tmp_path: Path
) -> None:
    remote.commit(
        {"real/slides.md": "# A\n", "examples/b/slides.md": "# B\n"}, at=_PUSHED_AT
    )
    remote.commit_symlink(path="examples/a", target="../real", at=_LATER)

    assert names_read(tmp_path, a_source(remote.url, folder="examples")) == {"b"}


def test_an_older_release_reading_a_source_with_a_folder_loses_its_nested_decks(
    remote: GitRemote, tmp_path: Path
) -> None:
    """SR9: a release that does not know the folder reads it as the top."""
    remote.commit({"examples/a/slides.md": "# Nested\n"}, at=_PUSHED_AT)
    nested = folders_read(tmp_path, a_source(remote.url, folder="examples"))
    remote.commit({"a/slides.md": "# Top level\n"}, at=_LATER)

    older = {f.name: f for f in folders_read(tmp_path, a_source(remote.url))}

    assert set(older) == {"a", "examples"}
    assert older["a"].commit != nested[0].commit


def test_the_plain_insert_stores_and_returns_the_folder(tmp_path: Path) -> None:
    store = a_source_store(a_source_database(tmp_path))

    added = store.add(
        a_source_write("https://example.invalid/a.git", folder="examples")
    )

    assert added is not None
    assert (added.folder, store.all()) == ("examples", (added,))


def test_the_insert_that_promotes_a_key_draft_stores_and_returns_the_folder(
    tmp_path: Path,
) -> None:
    database = a_source_database(tmp_path)
    drafted = a_key_drafts_store(database).get_or_mint(
        OWNER.id, newer_than=_PUSHED_AT, at=_LATER
    )
    store = a_source_store(database)

    added = store.add_from_draft(
        a_source_write("ssh://git@example.invalid/a.git", folder="talks/2026"),
        draft_id=drafted.id,
    )

    assert added is not None
    assert (added.folder, store.all()) == ("talks/2026", (added,))
