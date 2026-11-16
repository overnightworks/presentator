"""A tree the mirror cannot write makes that deck's build fail without raising."""

from pathlib import Path

import pytest

from presentator.contracts.decks import BuildFailure
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    builds_ready_for,
    the_error_line,
    with_the_toolchain,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.log_lines import json_logged

_A_NAME_PAST_THE_TARGET_FILESYSTEMS_NAME_LIMIT = "n" * 300


def test_a_tree_the_mirror_cannot_write_makes_that_decks_build_fail_without_raising(
    remote: GitRemote,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote.push_crafted_file(
        f"{EXAMPLE_SLUG}/{_A_NAME_PAST_THE_TARGET_FILESYSTEMS_NAME_LIMIT}"
    )
    source = a_source(remote.url)
    with_the_toolchain(monkeypatch, tmp_path, A_TOOLCHAIN_THAT_BUILDS)

    with json_logged(capsys) as logged:
        built = builds_ready_for(tmp_path, source).build(
            a_deck(EXAMPLE_SLUG, commit=remote.head), source=source
        )

    assert built == BuildFailure.without_words()
    assert the_error_line(logged)["reason"] == "unreadable_deck"
    assert EXAMPLE_SLUG not in logged.text
