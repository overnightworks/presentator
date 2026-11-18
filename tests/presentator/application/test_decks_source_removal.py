"""A removed source takes the runs its rows name and keeps the rest."""

import pytest

from presentator.application.decks import (
    Decks,
)
from presentator.ports.decks import RefreshOwnershipLostError
from tests.presentator.application.support.build_retention_support import (
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
from tests.presentator.application.support.refresh_ownership_fake import (
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
def test_removing_a_source_takes_the_runs_its_rows_name() -> None:
    """`remove_source` takes the runs its rows name."""
    builder, store, mirror, source_store = over_rows(
        a_row("one", build=a_build_in("one-run")),
        a_row("two", build=a_build_in("two-run")),
        on_disk=("one-run", "two-run", "foreign"),
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
        a_row("built", build=a_build_in("the-run")),
        on_disk=("the-run",),
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

    assert runs_left(builder) == {"the-run"}
    assert set(store.kept) == {"built"}
    assert source_store.sources == [THE_SOURCE]
