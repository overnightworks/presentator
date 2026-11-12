"""Provider-neutral values carried by the authenticated co-presenter path."""

from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto

from presentator.contracts.decks import Deck, Source
from presentator.contracts.models import Role
from presentator.contracts.voice import SampleLanguage


class CoPresenterUnavailableError(RuntimeError):
    """The private service or the gateway refused or could not satisfy an operation."""

    def __str__(self) -> str:
        """Keep the expected failure provider-neutral."""
        return "co-presenter unavailable"


class NotEnabledError(CoPresenterUnavailableError):
    """The gateway does not answer this person: they are not enabled for it."""


class GatewayBusyError(CoPresenterUnavailableError):
    """The gateway is busy now, and may name the seconds to wait before asking again."""

    def __init__(self, retry_after_s: int | None) -> None:
        """Carry the wait the gateway named, or none."""
        super().__init__()
        self.retry_after_s = retry_after_s


class BudgetSpentError(CoPresenterUnavailableError):
    """The person's gateway budget is spent; no outage."""


class AnswerStopReason(StrEnum):
    """The reason an answer stream's `error` event names, as the overlay reads it."""

    NOT_ENABLED = "not-enabled"
    BUSY = "busy"
    BUDGET_SPENT = "budget-spent"


class AccessRefusal(Enum):
    """Why a session yields no groups to answer a question for."""

    SIGN_IN_AGAIN = auto()
    SESSION_ENDED = auto()
    ISSUER_UNAVAILABLE = auto()


@dataclass(frozen=True, slots=True, kw_only=True)
class AskingPerson:
    """The signed-in person who asks: their account, their role and their groups now."""

    subject: str
    role: Role
    groups: frozenset[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class SpeechTokens:
    """The two tokens one use of the speech service carries; never shown."""

    person: str = field(repr=False)
    workload: str = field(repr=False)


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


@dataclass(frozen=True, slots=True, kw_only=True)
class ShowSlide:
    """The model moved the shown deck to this 1-based slide."""

    number: int


type AnswerEvent = AnswerText | Sentence | Audio | Done | ShowSlide


@dataclass(frozen=True, slots=True, kw_only=True)
class HearingTranscript:
    """One interim or final transcript from private hearing.

    A final one carries the language its utterance was heard in.
    """

    text: str
    final: bool
    language: SampleLanguage | None = None

    def __post_init__(self) -> None:
        """Refuse a final transcript that names no language."""
        if self.final and self.language is None:
            message = "a final transcript names its language"
            raise ValueError(message)


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
