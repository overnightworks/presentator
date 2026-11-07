"""The deck text reader as a test double, and the words it reads."""

from dataclasses import dataclass, field

from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    DeckText,
    SlideText,
)
from presentator.contracts.decks import Deck, Source

A_DECK_TEXT = DeckText(
    title="Kundenfeedback",
    language="de",
    slides=(
        SlideText(number=1, title="Start", body="Willkommen", notes=""),
        SlideText(number=2, title="Wichtig", body="Der Kern", notes="Betonen"),
    ),
)


@dataclass
class FakeDeckTexts:
    """Reads the same words for every deck unless `by_slug` names that deck's own."""

    text: DeckText | None = field(default_factory=lambda: A_DECK_TEXT)
    unreadable: bool = False
    by_slug: dict[str, DeckText | None] = field(
        default_factory=dict[str, DeckText | None]
    )
    asked: list[tuple[Deck, Source]] = field(default_factory=list[tuple[Deck, Source]])

    def text_of(self, deck: Deck, source: Source) -> DeckText | None:
        self.asked.append((deck, source))
        if self.unreadable:
            raise CoPresenterUnavailableError
        return self.by_slug.get(deck.slug, self.text)
