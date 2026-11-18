"""A delivered build is the run the store took, and nothing the store did not."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.contracts.decks import BuildRun
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    builds_ready_for,
    what_it_built,
    with_the_toolchain,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.run_objects import DirectoryRunObjects

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    "answering", [True, False], ids=["a store that takes it", "a store that is down"]
)
def test_a_delivered_build_names_the_one_run_the_store_took(
    remote: GitRemote,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    answering: bool,  # noqa: FBT001 - the parametrized case
) -> None:
    with_the_toolchain(monkeypatch, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=_PUSHED_AT)
    source = a_source(remote.url)
    builds = builds_ready_for(tmp_path, source)
    deck = a_deck(EXAMPLE_SLUG, commit=remote.head)
    artefacts = what_it_built(builds.build(deck, source=source))
    store = builds.cache.store
    assert isinstance(store, DirectoryRunObjects)
    store.answering = answering

    delivered = builds.deliver(artefacts)

    store.answering = True
    stored = store.names()
    assert stored is not None
    assert {BuildRun(name) for name in stored} == ({delivered} if answering else set())
    assert (delivered is None) is not answering
