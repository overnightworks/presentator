"""The viewer's own built deck and its words: the one place that decides whose it is."""

from dataclasses import dataclass

from presentator.contracts.copresenter import AskedDeck, DeckText
from presentator.contracts.decks import is_a_plain_folder_name
from presentator.ports.copresenter import DeckTexts
from presentator.ports.decks import DeckStore, SourceStore


@dataclass(frozen=True, slots=True, kw_only=True)
class OwnDecks:
    """What a viewer may read of their own built decks, for every host."""

    decks: DeckStore
    sources: SourceStore
    texts: DeckTexts

    def asked_deck(self, slug: str, *, viewer: str) -> AskedDeck | None:
        """The viewer's own built deck and its source, or nothing for any other."""
        if not is_a_plain_folder_name(slug):
            return None
        deck = self.decks.get(slug)
        if deck is None or not deck.is_owned_by(viewer) or deck.build is None:
            return None
        source = next(
            (item for item in self.sources.all() if item.id == deck.source_id), None
        )
        return None if source is None else AskedDeck(deck=deck, source=source)

    def text_of(self, slug: str, *, viewer: str) -> DeckText | None:
        """The words of the viewer's own built deck, or nothing for any other.

        Nothing also where the built commit carries no slides. Raises
        CoPresenterUnavailableError when the mirror cannot be read.
        """
        asked = self.asked_deck(slug, viewer=viewer)
        return None if asked is None else self.texts.text_of(asked.deck, asked.source)
