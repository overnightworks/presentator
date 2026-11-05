"""A deck whose built talk vanished from disk is built again."""

import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentator.application.decks import Decks
from presentator.contracts.decks import BuildOutcome, SourceRunOutcome
from presentator.ports.decks import BuildRun
from tests.presentator.application.support.decks_support import (
    DecksFakes,
    a_folder,
    decks_over,
    having,
)
from tests.presentator.application.support.fakes import BUILDS_ROOT
from tests.support.deck_source_support import a_source, folders_under, mirrors_under
from tests.support.git_remote import GitRemote

_A_DECK = "kundenfeedback"
_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_A_TALK = {
    f"{_A_DECK}/deck.toml": 'title = "Feedback"\n',
    f"{_A_DECK}/slides.md": "# Feedback\n",
}


def a_built_deck() -> tuple[Decks, DecksFakes]:
    fakes = DecksFakes()
    decks = decks_over(a_folder(_A_DECK), fakes=fakes)
    decks.refresh()
    assert fakes.builder.built == [_A_DECK]
    return decks, fakes


def its_run_vanishes(fakes: DecksFakes) -> None:
    fakes.builder.on_disk.clear()


def the_standing_run_is_on_disk(fakes: DecksFakes) -> bool:
    standing = fakes.store.kept[_A_DECK].build
    assert standing is not None
    runs = {
        fakes.builder.run_of(standing.directory),
        fakes.builder.run_of(standing.pdf),
    }
    return runs <= fakes.builder.on_disk


def test_a_deck_whose_run_vanished_is_built_again_at_the_next_refresh() -> None:
    decks, fakes = a_built_deck()
    its_run_vanishes(fakes)

    decks.refresh()

    assert fakes.builder.built == [_A_DECK, _A_DECK]
    assert the_standing_run_is_on_disk(fakes)


def test_a_deck_whose_run_is_on_disk_is_not_built_again() -> None:
    decks, fakes = a_built_deck()

    decks.refresh()

    assert fakes.builder.built == [_A_DECK]


@pytest.mark.parametrize("vanished", ["talk-run", "pdf-run"])
def test_a_deck_whose_talk_or_pdf_run_alone_vanished_is_built_again(
    vanished: str,
) -> None:
    decks, fakes = a_built_deck()
    standing = fakes.store.kept[_A_DECK].build
    assert standing is not None
    fakes.store.put_build(
        _A_DECK,
        replace(
            standing,
            directory=BUILDS_ROOT / "talk-run" / "out" / "talk",
            pdf=BUILDS_ROOT / "pdf-run" / "out" / "deck.pdf",
        ),
    )
    fakes.builder.on_disk = {BuildRun("talk-run"), BuildRun("pdf-run")} - {
        BuildRun(vanished)
    }

    decks.refresh()

    assert fakes.builder.built == [_A_DECK, _A_DECK]


def test_a_listing_that_did_not_finish_builds_nothing() -> None:
    decks, fakes = a_built_deck()
    its_run_vanishes(fakes)
    fakes.builder.listing_fails = True

    decks.refresh()

    assert fakes.builder.built == [_A_DECK]


def test_a_vanished_run_whose_rebuild_fails_is_not_built_again_per_refresh() -> None:
    decks, fakes = a_built_deck()
    its_run_vanishes(fakes)
    fakes.builder.fails = True
    decks.refresh()

    decks.refresh()
    decks.refresh()

    assert fakes.builder.built == [_A_DECK, _A_DECK]
    attempt = fakes.store.kept[_A_DECK].attempt
    assert attempt is not None
    assert attempt.outcome is BuildOutcome.FAILED


def test_a_source_whose_mirror_is_gone_is_cloned_again_at_its_next_take_in(
    remote: GitRemote, tmp_path: Path
) -> None:
    source = a_source(remote.url)
    remote.commit(_A_TALK, at=_PUSHED_AT)
    fakes = DecksFakes()
    decks = decks_over(
        sources=having(source),
        mirror=folders_under(tmp_path),
        fakes=fakes,
    )
    decks.refresh()
    mirror = mirrors_under(tmp_path).of(source)
    assert mirror is not None
    shutil.rmtree(mirror.directory)

    decks.refresh()

    newest = fakes.source_runs.newest(source.id)
    assert newest is not None
    assert newest.outcome is SourceRunOutcome.SUCCESS
    assert mirror.directory.is_dir()
