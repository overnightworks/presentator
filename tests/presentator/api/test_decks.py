"""A pushed deck folder, taken in, is what `GET /api/decks` lists."""

from datetime import timedelta
from typing import Final

from presentator.contracts.decks import MANIFEST_FILE, SLIDES_FILE, DeckFolder
from tests.support.lobby import (
    NOW,
    GivenDecks,
    a_configured_source,
    a_signed_in_lobby,
    deck_list,
)

_ADDRESS: Final = "git@heimserver:decks.git"
_A_DECK: Final = frozenset({MANIFEST_FILE, SLIDES_FILE})
_COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


def a_folder(name: str, *, title: str, changed_ago: timedelta) -> DeckFolder:
    return DeckFolder(
        name=name,
        file_names=_A_DECK,
        title=title,
        changed_at=NOW - changed_ago,
        commit=_COMMIT,
    )


def test_a_pushed_deck_is_listed_with_its_title_and_its_folder() -> None:
    lobby = a_signed_in_lobby(
        GivenDecks(
            folders=(
                a_folder(
                    "kundenfeedback",
                    title="Kundenfeedback Q3",
                    changed_ago=timedelta(minutes=2),
                ),
            ),
            source=a_configured_source(_ADDRESS),
        ),
    )

    [listed] = deck_list(lobby).decks

    assert (listed.slug, listed.title) == ("kundenfeedback", "Kundenfeedback Q3")
    assert listed.changed_at == NOW - timedelta(minutes=2)


def test_the_most_recently_changed_pushed_deck_stands_first() -> None:
    lobby = a_signed_in_lobby(
        GivenDecks(
            folders=(
                a_folder("older", title="Older talk", changed_ago=timedelta(days=6)),
                a_folder("newer", title="Newer talk", changed_ago=timedelta(minutes=2)),
            ),
            source=a_configured_source(_ADDRESS),
        ),
    )

    assert [deck.slug for deck in deck_list(lobby).decks] == ["newer", "older"]
