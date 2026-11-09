"""A tree the mirror cannot write makes that deck's build fail without raising."""

from pathlib import Path

import pytest

from presentator.contracts.decks import BuildFailure
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    builds_ready_for,
    with_the_toolchain,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote

_A_NAME_PAST_THE_TARGET_FILESYSTEMS_NAME_LIMIT = "n" * 300


def test_a_tree_the_mirror_cannot_write_makes_that_decks_build_fail_without_raising(
    remote: GitRemote,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    remote.push_crafted_file(
        f"{EXAMPLE_SLUG}/{_A_NAME_PAST_THE_TARGET_FILESYSTEMS_NAME_LIMIT}"
    )
    source = a_source(remote.url)
    with_the_toolchain(monkeypatch, tmp_path, A_TOOLCHAIN_THAT_BUILDS)

    built = builds_ready_for(tmp_path, source).build(
        a_deck(EXAMPLE_SLUG, commit=remote.head), source=source
    )

    assert built == BuildFailure.without_words()
    assert EXAMPLE_SLUG in caplog.text
