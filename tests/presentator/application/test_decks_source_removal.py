"""A removed source takes the runs its rows name and keeps the rest."""

import pytest

from presentator.application.decks import (
    Decks,
    SourceRemovalRefusal,
    SourceRemovalRefused,
)
from presentator.contracts.decks import Build
from presentator.ports.decks import RefreshOwnershipLostError
from tests.presentator.application.support.build_retention_support import (
    THE_COMMIT,
    THE_MOMENT,
    THE_SOURCE,
    a_build_in,
    a_row,
    over_rows,
    runs_left,
)
from tests.presentator.application.support.decks_support import (
    DecksFakes,
    decks_over,
)
from tests.presentator.application.support.fakes import (
    BUILDS_ROOT,
    FakeRefreshOwnership,
)


def _confirmed(decks: Decks, name: str) -> object:
    preview = decks.removal(name)
    assert preview is not None
    return decks.remove_source(
        name,
        expected_deck_count=preview.deck_count,
        expected_run_count=preview.run_count,
    )


# DK10
def test_removing_a_source_takes_the_runs_of_its_talk_and_its_pdf() -> None:
    """`remove_source` takes the runs its rows name."""
    builder, store, mirror, source_store = over_rows(
        a_row("split", build=a_build_in("talk-run", "pdf-run")),
        on_disk=("talk-run", "pdf-run", "foreign"),
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    _confirmed(decks, THE_SOURCE.name)

    assert runs_left(builder) == {"foreign"}
    assert store.kept == {}


def test_a_removal_that_lost_the_refresh_leaves_every_build_and_row() -> None:
    builder, store, mirror, source_store = over_rows(
        a_row("split", build=a_build_in("talk-run", "pdf-run")),
        on_disk=("talk-run", "pdf-run"),
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(
            builder=builder,
            store=store,
            ownership=FakeRefreshOwnership(lost_to_another=True),
        ),
    )

    with pytest.raises(RefreshOwnershipLostError):
        _confirmed(decks, THE_SOURCE.name)

    assert runs_left(builder) == {"talk-run", "pdf-run"}
    assert set(store.kept) == {"split"}
    assert source_store.sources == [THE_SOURCE]


def test_removing_a_source_over_a_build_no_run_owns_keeps_every_row() -> None:
    """`remove_source` over an unplaced build answers DISK_LEFTOVER."""
    builder, store, mirror, source_store = over_rows(
        a_row(
            "odd",
            build=Build(
                directory=BUILDS_ROOT / "talk",
                pdf=BUILDS_ROOT / "deck.pdf",
                commit=THE_COMMIT,
                built_at=THE_MOMENT,
                toolchain=None,
            ),
        ),
        on_disk=("other",),
    )
    decks = decks_over(
        sources=source_store,
        mirror=mirror,
        fakes=DecksFakes(builder=builder, store=store),
    )

    outcome = _confirmed(decks, THE_SOURCE.name)

    assert outcome == SourceRemovalRefused(
        reason=SourceRemovalRefusal.DISK_LEFTOVER, mirror=None, builds=None
    )
    assert set(store.kept) == {"odd"}
    assert source_store.sources == [THE_SOURCE]
    assert runs_left(builder) == {"other"}
