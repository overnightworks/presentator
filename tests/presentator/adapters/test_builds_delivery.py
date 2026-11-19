"""A delivered build is the run the store took, and its line says whether it did."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.adapters.builds import BuildFault, SlidevBuilds
from presentator.contracts.decks import (
    Artefacts,
    BuildFailure,
    BuildFailureReason,
    Deck,
    Source,
    run_name_holding,
)
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    builds_ready_for,
    lines_of,
    with_the_toolchain,
)
from tests.support.deck_source_support import a_deck, a_source
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.log_lines import json_logged
from tests.support.run_objects import DirectoryRunObjects

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)


@pytest.fixture
def ready(
    remote: GitRemote, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[SlidevBuilds, Deck, Source]:
    """The adapter over a store of its own, and a deck its toolchain builds."""
    with_the_toolchain(monkeypatch, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=_PUSHED_AT)
    source = a_source(remote.url)
    deck = a_deck(EXAMPLE_SLUG, commit=remote.head)
    return builds_ready_for(tmp_path, source), deck, source


def test_a_delivered_build_names_the_one_run_the_store_took(
    ready: tuple[SlidevBuilds, Deck, Source], capsys: pytest.CaptureFixture[str]
) -> None:
    builds, deck, source = ready

    with json_logged(capsys) as logged:
        built = builds.build(deck, source=source)

    assert isinstance(built, Artefacts)
    assert builds.cache.store.names() == {run_name_holding(built.directory)}
    (ended,) = lines_of(logged, "deck_build.ended")
    assert ended["outcome"] == "ok"


def test_a_build_the_store_does_not_take_fails_and_its_line_says_so(
    ready: tuple[SlidevBuilds, Deck, Source], capsys: pytest.CaptureFixture[str]
) -> None:
    builds, deck, source = ready
    store = builds.cache.store
    assert isinstance(store, DirectoryRunObjects)
    store.answering = False

    with json_logged(capsys) as logged:
        built = builds.build(deck, source=source)

    assert built == BuildFailure(reason=BuildFailureReason.STORE)
    (ended,) = lines_of(logged, "deck_build.ended")
    assert (ended["outcome"], ended["reason"]) == ("failed", BuildFault.NOT_KEPT)
    store.answering = True
    assert store.names() == frozenset()
    assert list(builds.builds.iterdir()) == []
