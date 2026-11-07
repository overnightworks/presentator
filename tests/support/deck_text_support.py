"""The deck text reader as a test double, and the words it reads."""

from dataclasses import dataclass, field

from gitmirror.model import MirrorError
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
A_MIRROR_PATH = "/var/mirrors/the-source/felix-deck/slides.md"


@dataclass
class FakeDeckTexts:
    """Reads the same words for every deck unless `by_slug` or `by_commit` says else.

    An unreadable mirror fails the way the real one does: from a git error that
    names a path on the mirror's disk.
    """

    text: DeckText | None = field(default_factory=lambda: A_DECK_TEXT)
    unreadable: bool = False
    by_slug: dict[str, DeckText | None] = field(
        default_factory=dict[str, DeckText | None]
    )
    by_commit: dict[str, DeckText | None] = field(
        default_factory=dict[str, DeckText | None]
    )
    asked: list[tuple[Deck, Source]] = field(default_factory=list[tuple[Deck, Source]])

    def text_of(self, deck: Deck, source: Source) -> DeckText | None:
        self.asked.append((deck, source))
        if self.unreadable:
            raise CoPresenterUnavailableError from MirrorError(
                f"cannot read {A_MIRROR_PATH}"
            )
        built = self.by_slug.get(deck.slug, self.text)
        if deck.build is None:
            return built
        return self.by_commit.get(deck.build.commit, built)
