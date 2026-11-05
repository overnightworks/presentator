"""Provider-neutral values carried by the authenticated co-presenter path."""

from dataclasses import dataclass, field
from enum import Enum, auto

from presentator.contracts.decks import Deck, Source
from presentator.contracts.models import Role


class CoPresenterUnavailableError(RuntimeError):
    """The private service or the gateway refused or could not satisfy an operation."""

    def __str__(self) -> str:
        """Keep the expected failure provider-neutral."""
        return "co-presenter unavailable"


class NotEnabledError(CoPresenterUnavailableError):
    """The gateway does not answer this person: they are not enabled for it."""


class AccessRefusal(Enum):
    """Why a session yields no groups to answer a question for."""

    SIGN_IN_AGAIN = auto()
    SESSION_ENDED = auto()
    ISSUER_UNAVAILABLE = auto()


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechTokens:
    """The two access tokens the speech service takes: the person's and the app's.

    Neither shows in a repr, so a record of the object cannot leak one.
    """

    person: str = field(repr=False)
    workload: str = field(repr=False)


@dataclass(frozen=True, slots=True, kw_only=True)
class AskingPerson:
    """The signed-in person who asks: their account, their role and their groups now."""

    subject: str
    role: Role
    groups: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class AskedDeck:
    """A built deck its asker owns, with the source that carries it."""

    deck: Deck
    source: Source


@dataclass(frozen=True, slots=True, kw_only=True)
class Question:
    """What a person asked while showing one slide."""

    said: str
    slide: int
    language: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class CoPresenterReadiness:
    """The readiness facts the repository overlay consumes."""

    hearing_sample_rate: int
    local_hearing_ready: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class AnswerText:
    """One incremental answer fragment."""

    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Sentence:
    """One complete answer sentence."""

    text: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Audio:
    """The spoken WAV for one answer sentence."""

    text: str
    wav: bytes


@dataclass(frozen=True, slots=True, kw_only=True)
class Done:
    """The complete answer text."""

    text: str


type AnswerEvent = AnswerText | Sentence | Audio | Done


@dataclass(frozen=True, slots=True, kw_only=True)
class HearingTranscript:
    """One interim or final transcript from private hearing."""

    text: str
    final: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class SlideText:
    """One slide's words as its deck carries them."""

    number: int
    title: str
    body: str
    notes: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DeckText:
    """The words of one deck at its built commit; no language means unnamed."""

    title: str
    language: str | None
    slides: tuple[SlideText, ...]

    def at(self, number: int) -> SlideText | None:
        """The slide with this 1-based number, or nothing."""
        return next((slide for slide in self.slides if slide.number == number), None)
