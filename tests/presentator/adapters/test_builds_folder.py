"""A deck under its source's folder is exported from there, never from the top."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.contracts.decks import Source
from tests.presentator.adapters.support.build_support import (
    builds_ready_for,
    what_it_built,
    with_the_toolchain,
)
from tests.support.deck_source_support import (
    a_deck,
    a_source,
    a_source_database,
    a_source_store,
    a_source_write,
)
from tests.support.git_remote import GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
# Writes down the names of the folder it was asked to build a deck from.
_A_TOOLCHAIN_THAT_LISTS_THE_DECK = """#!/bin/sh
set -eu
out=""
deck_folder=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *slides.md) deck_folder="$(dirname "$1")"; shift;;
        *) shift;;
    esac
done
case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *) mkdir -p "$out"; ls "$deck_folder" > "$out/index.html";;
esac
"""


def test_the_export_holds_only_the_nested_decks_own_tree(
    remote: GitRemote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    remote.commit(
        {
            "examples/a/slides.md": "# Nested\n",
            "examples/a/only-nested.png": "x",
            "a/slides.md": "# Top level\n",
            "a/only-top.png": "x",
        },
        at=_PUSHED_AT,
    )
    store = a_source_store(a_source_database(tmp_path))
    store.add(a_source_write(remote.url, folder="examples"))
    (stored,) = store.all()
    deck = a_deck("a", commit=remote.head)
    with_the_toolchain(monkeypatch, tmp_path, _A_TOOLCHAIN_THAT_LISTS_THE_DECK)
    builds = builds_ready_for(tmp_path, stored)

    artefacts = what_it_built(builds.build(deck, source=stored))

    listed = (artefacts.directory / "index.html").read_text(encoding="utf-8").split()
    assert sorted(listed) == ["only-nested.png", "slides.md"]


def test_a_source_without_a_folder_still_exports_the_top_level_deck(
    remote: GitRemote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    remote.commit(
        {"a/slides.md": "# Top level\n", "examples/a/slides.md": "# Nested\n"},
        at=_PUSHED_AT,
    )
    source: Source = a_source(remote.url)
    with_the_toolchain(monkeypatch, tmp_path, _A_TOOLCHAIN_THAT_LISTS_THE_DECK)
    builds = builds_ready_for(tmp_path, source)

    artefacts = what_it_built(
        builds.build(a_deck("a", commit=remote.head), source=source)
    )

    listed = (artefacts.directory / "index.html").read_text(encoding="utf-8").split()
    assert listed == ["slides.md"]
