"""Lifetime ownership for private co-presenter operations."""

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractContextManager, aclosing, asynccontextmanager
from dataclasses import dataclass, field
from threading import Lock

from presentator.application.copresenter_prompt import user_message
from presentator.application.own_decks import OwnDecks
from presentator.application.spoken_answer import (
    deck_language_of,
    spoken_events,
    spoken_language,
)
from presentator.contracts.copresenter import (
    AccessRefusal,
    AnswerEvent,
    AskedDeck,
    AskingPerson,
    Audio,
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    Question,
    SpeechTokens,
)
from presentator.contracts.models import Role
from presentator.contracts.voice import SampleLanguage
from presentator.ports.clock import Clock
from presentator.ports.copresenter import (
    AgentAnswers,
    DeckTexts,
    PersonAccess,
    PrivateHearing,
    SentenceBoundaries,
    Speaking,
    SpeechHearing,
)
from presentator.ports.decks import DeckStore, SourceStore

type CoPresenterHearing = PrivateHearing

_LOGGER = logging.getLogger(__name__)
_MILLISECONDS = 1000


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAsk:
    """An ask whose person's groups and speech tokens are in hand, and when it began."""

    asked: AskedDeck
    person: AskingPerson
    speech: SpeechTokens
    started: float


@dataclass(frozen=True, slots=True, kw_only=True)
class CoPresenterUse:
    """Use at most one private operation and close every operation it opens."""

    answers: AgentAnswers
    speech: SpeechHearing
    decks: DeckStore
    sources: SourceStore
    texts: DeckTexts
    boundaries: SentenceBoundaries
    speaking: Speaking
    person_access: PersonAccess
    monotonic: Callable[[], float] = time.monotonic

    def asked_deck(self, slug: str, *, viewer: str) -> AskedDeck | None:
        """The viewer's own built deck and its source, or nothing for any other."""
        return OwnDecks(
            decks=self.decks, sources=self.sources, texts=self.texts
        ).asked_deck(slug, viewer=viewer)

    async def deck_language(self, asked: AskedDeck) -> SampleLanguage:
        """The language the deck's manifest names, German when it names none.

        Raises CoPresenterUnavailableError when its words cannot be read.
        """
        deck_text = await asyncio.to_thread(
            self.texts.text_of, asked.deck, asked.source
        )
        if deck_text is None:
            raise CoPresenterUnavailableError
        return deck_language_of(deck_text.language)

    async def readiness(self) -> CoPresenterReadiness:
        """Return speech's narrow readiness report."""
        return await self.speech.readiness()

    async def prepare(
        self, asked: AskedDeck, *, session_id: str, subject: str, role: Role
    ) -> PreparedAsk | AccessRefusal:
        """Read the asking person's groups and speech tokens before anything streams.

        Raises CoPresenterUnavailableError when either cannot be read.
        """
        started = self.monotonic()
        groups = await self.person_access.groups_of(session_id)
        if isinstance(groups, AccessRefusal):
            return groups
        speech = await self.person_access.speech_tokens_of(session_id)
        if isinstance(speech, AccessRefusal):
            return speech
        person = AskingPerson(subject=subject, role=role, groups=groups)
        return PreparedAsk(asked=asked, person=person, speech=speech, started=started)

    @asynccontextmanager
    async def answer(
        self, prepared: PreparedAsk, question: Question
    ) -> AsyncGenerator[AsyncIterator[AnswerEvent]]:
        """Stream the spoken answer for as long as its caller reads it."""
        deck_text = await asyncio.to_thread(
            self.texts.text_of, prepared.asked.deck, prepared.asked.source
        )
        if deck_text is None:
            raise CoPresenterUnavailableError
        language = spoken_language(question.language, deck_text.language)

        async with self.answers.answer(
            message=user_message(question, deck_text), person=prepared.person
        ) as texts:
            events = spoken_events(
                texts, self.boundaries, self.speaking, language, prepared.speech
            )
            async with aclosing(self._timed(events, prepared.started)) as timed:
                yield timed

    async def _timed(
        self, events: AsyncIterator[AnswerEvent], started: float
    ) -> AsyncGenerator[AnswerEvent]:
        first_audio_pending = True
        async for event in events:
            if first_audio_pending and isinstance(event, Audio):
                first_audio_pending = False
                elapsed = self.monotonic() - started
                _LOGGER.info("co-presenter first audio: ms=%d", elapsed * _MILLISECONDS)
            yield event

    @asynccontextmanager
    async def hear(
        self, language: str, *, session_id: str
    ) -> AsyncGenerator[PrivateHearing]:
        """Read the session's speech tokens, then close the child on every exit.

        Raises CoPresenterUnavailableError when the tokens cannot be had.
        """
        tokens = await self.person_access.speech_tokens_of(session_id)
        if isinstance(tokens, AccessRefusal):
            raise CoPresenterUnavailableError
        async with self.speech.hear(language, tokens) as hearing:
            try:
                yield hearing
            finally:
                await hearing.aclose()


BUSY_RETRY_SECONDS = 1


@dataclass(frozen=True, slots=True)
class RetryAfter:
    """A refused question and the whole seconds until it may be asked again."""

    seconds: int


@dataclass(eq=False, slots=True)
class AskTicket:
    """The one answer a session is allowed to stream right now."""

    session_id: str
    _answering: dict[str, AskTicket]
    _guard: AbstractContextManager[bool]

    def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing."""
        with self._guard:
            if self._answering.get(self.session_id) is self:
                del self._answering[self.session_id]


@dataclass(slots=True)
class AskLimiter:
    """Admit one streaming answer per session at a time.

    A session leaves no record once nothing of it is streaming, so a signed-out
    session cannot make this grow.

    Safe to call from any thread: every check and update runs under one lock,
    so concurrent asks cannot interleave between them.
    """

    clock: Clock
    _answering: dict[str, AskTicket] = field(default_factory=dict[str, AskTicket])
    guard: AbstractContextManager[bool] = field(default_factory=Lock)

    def admit(self, session_id: str) -> AskTicket | RetryAfter:
        """Hand out the session's ticket, or say when to retry."""
        with self.guard:
            return self._admit(session_id)

    def _admit(self, session_id: str) -> AskTicket | RetryAfter:
        if session_id in self._answering:
            return RetryAfter(BUSY_RETRY_SECONDS)
        ticket = AskTicket(session_id, self._answering, self.guard)
        self._answering[session_id] = ticket
        return ticket
