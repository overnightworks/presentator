"""Lifetime ownership for private co-presenter operations."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from presentator.application.copresenter_prompt import user_message
from presentator.application.own_decks import OwnDecks
from presentator.application.spoken_answer import spoken_events, voice_language_of
from presentator.contracts.copresenter import (
    AccessRefusal,
    AnswerEvent,
    AskedDeck,
    AskingPerson,
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
    Question,
    SpeechTokens,
    SpeechTokenSource,
)
from presentator.contracts.models import Role
from presentator.contracts.voice import SampleLanguage
from presentator.ports.copresenter import (
    AgentAnswers,
    AskLimiter,
    AskTicket,
    DeckTexts,
    HearingSocket,
    PersonAccess,
    SentenceBoundaries,
    Speaking,
    SpeechHearing,
)
from presentator.ports.decks import DeckStore, SourceStore

# The routes name the ask lease through this module, never a port.
__all__ = ["AskLimiter", "AskTicket", "CoPresenterUse", "PreparedAsk"]


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedAsk:
    """An ask whose person's groups and speech tokens are in hand."""

    asked: AskedDeck
    person: AskingPerson
    speech: SpeechTokens


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
        return voice_language_of(deck_text.language)

    async def readiness(self) -> CoPresenterReadiness:
        """Return speech's narrow readiness report."""
        return await self.speech.readiness()

    async def prepare(
        self, asked: AskedDeck, *, session_id: str, subject: str, role: Role
    ) -> PreparedAsk | AccessRefusal:
        """Read the asking person's groups and speech tokens before anything streams.

        Raises CoPresenterUnavailableError when either cannot be read.
        """
        groups = await self.person_access.groups_of(session_id)
        if isinstance(groups, AccessRefusal):
            return groups
        speech = await self.person_access.speech_tokens_of(session_id)
        if isinstance(speech, AccessRefusal):
            return speech
        person = AskingPerson(subject=subject, role=role, groups=groups)
        return PreparedAsk(asked=asked, person=person, speech=speech)

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
        async with self.answers.answer(
            message=user_message(question, deck_text), person=prepared.person
        ) as texts:
            events = spoken_events(
                texts,
                self.boundaries,
                self.speaking,
                question.language,
                prepared.speech,
            )
            yield events

    async def hearing_tokens(self, session_id: str) -> SpeechTokenSource:
        """The session's speech tokens, read now, for the route to hold while it hears.

        The source gives these for the first opening of the service and tokens read
        afresh for each later one. Raises CoPresenterUnavailableError when they
        cannot be had.
        """
        first = await self._read_tokens(session_id)
        return _OpeningTokens(first, lambda: self._read_tokens(session_id))

    async def _read_tokens(self, session_id: str) -> SpeechTokens:
        tokens = await self.person_access.speech_tokens_of(session_id)
        if isinstance(tokens, AccessRefusal):
            raise CoPresenterUnavailableError
        return tokens

    async def relay_hearing(
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokenSource,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> None:
        """Hear for the person whose tokens these are until the hearing ends."""
        await self.speech.relay(browser, language, tokens, ended=ended, heard=heard)

    async def refuse_hearing(self, browser: HearingSocket) -> None:
        """Close the browser as a hearing the service cannot give."""
        await self.speech.refuse(browser)


@dataclass(slots=True)
class _OpeningTokens:
    """The tokens of each opening of a hearing: the one in hand, then fresh ones."""

    first: SpeechTokens | None
    read_fresh: Callable[[], Awaitable[SpeechTokens]]

    async def __call__(self) -> SpeechTokens:
        if self.first is None:
            return await self.read_fresh()
        tokens, self.first = self.first, None
        return tokens
