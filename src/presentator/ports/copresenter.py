"""The typed private capabilities used by the co-presenter application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from presentator.contracts.copresenter import (
        AccessRefusal,
        AskingPerson,
        Audio,
        CoPresenterReadiness,
        CoPresenterUnavailableError,
        DeckText,
        HeardInTalk,
        HearingTranscript,
        RetryAfter,
        ShowSlide,
        SpeechTokens,
        SpeechTokenSource,
        TalkEvent,
    )
    from presentator.contracts.decks import Deck, Source
    from presentator.contracts.models import Role
    from presentator.contracts.words import Language


class HearingSocket(Protocol):
    """What a hearing calls on the browser's socket; FastAPI's WebSocket fits."""

    async def receive(self) -> Mapping[str, object]:
        """The next ASGI message of the socket."""
        ...

    async def close(self, code: int) -> None:
        """Close the socket with `code`."""
        ...


class AgentAnswers(Protocol):
    """The text of a model's answer to one message, streamed."""

    def answer(
        self, *, message: str, person: AskingPerson
    ) -> AbstractAsyncContextManager[AsyncIterator[str | ShowSlide]]:
        """Open one answer: the text deltas and the slides it shows, until it ends.

        Raises CoPresenterUnavailableError when the answer cannot be had, and
        NotEnabledError when the gateway does not answer this person.
        """
        ...


class SpeechHearing(Protocol):
    """The hearing the shared speech service offers Presentator."""

    async def readiness(self) -> CoPresenterReadiness:
        """Read the readiness facts the browser consumes."""
        ...

    async def relay(  # noqa: PLR0913 - one parameter per input of the hearing
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokenSource,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
        text: Callable[[str], None] | None = None,
    ) -> None:
        """Hear for the person whose tokens `tokens` reads until the hearing ends.

        Relays the browser's audio to the service and hands each transcript to
        `heard`. The service is reopened after a drain or a cut and `tokens` is
        read again for each opening. Closes the browser socket with the code of
        how the hearing ended, and ends it at once when `ended` is set; tokens
        that cannot be read end it as a service failure. A browser that left,
        or an unexpected failure, passes to the caller.

        Each text frame of the browser goes to `text` unread and synchronously,
        in order with the audio; without `text` a text frame ends the hearing.
        An `UnreadableTalkMessageError` that `text` raises ends the hearing
        refused and passes to the caller unchanged.
        """
        ...

    async def refuse(
        self, browser: HearingSocket, unavailable: CoPresenterUnavailableError
    ) -> None:
        """Close the browser socket on a hearing its tokens could not be read for.

        A session that ended closes in order, as a sign-out does; anything else
        closes as a hearing the service cannot give.
        """
        ...


class DeckTexts(Protocol):
    """Reads the words of a deck's built commit."""

    def text_of(self, deck: Deck, source: Source) -> DeckText | None:
        """The deck's words, or nothing while it has no build or no slides.

        Raises CoPresenterUnavailableError when its mirror cannot be read.
        """
        ...


SentenceBoundaries = Callable[[str], tuple[str, ...]]
"""Splits text into its sentences; the last may still be unfinished."""


class Speaking(Protocol):
    """Speaks one sentence through the shared speech service."""

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        """The sentence as one whole WAV.

        Raises CoPresenterUnavailableError when speech cannot speak it.
        """
        ...


class PersonAccess(Protocol):
    """Gives what the issuer grants a signed-in person now."""

    async def groups_of(self, session_id: str) -> frozenset[str] | AccessRefusal:
        """The session's person's groups, or why it has none.

        Raises CoPresenterUnavailableError when the session store or a worker
        thread fails.
        """
        ...

    async def speech_tokens_of(self, session_id: str) -> SpeechTokens | AccessRefusal:
        """The two tokens the speech service takes, or why there are none.

        Raises CoPresenterUnavailableError when the session store or a worker
        thread fails.
        """
        ...


class AskTicket(Protocol):
    """The one answer a session is allowed to stream right now."""

    @property
    def session_id(self) -> str:
        """The session the answer belongs to."""
        ...

    async def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing.

        A store that does not answer leaves the hold to expire on its own.
        """
        ...


class AskLimiter(Protocol):
    """Admits one streaming answer per session at a time, across every process."""

    async def admit(self, session_id: str) -> AskTicket | RetryAfter:
        """Hand out the session's ticket, or say when to retry.

        Raises CoPresenterUnavailableError when the lease cannot be told.
        """
        ...


class TalkBrowser(Protocol):
    """The route's side of a talk: the sound it plays and the deck it shows."""

    async def play(self, audio: Audio) -> None:
        """Play the sound and return when the browser says it ended.

        Cancelling the call stops the sound. Raises SoundRefusedError when the
        browser could not play it.
        """
        ...

    def show_slide(self, number: int) -> None:
        """Tell the browser the model moved the deck to this slide."""
        ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Talk:
    """One talk the model hears through: who, what it knows and where it speaks."""

    session_id: str
    subject: str
    role: Role
    groups: Callable[[], Awaitable[frozenset[str] | AccessRefusal]]
    """The person's groups now, read at each decision."""
    context: str
    speak: Callable[[str, str], Awaitable[bytes]]
    """One sentence in a language as a WAV, with tokens read at each call."""
    controls_language: Language
    limiter: AskLimiter
    browser: TalkBrowser
    hearing: AsyncIterator[HeardInTalk]
    report: Callable[[TalkEvent], None]


class CoPresenterTalks(Protocol):
    """Runs a talk's model: every line it hears wakes a decision it may answer."""

    async def run(self, talk: Talk) -> None:
        """Hear and answer until the hearing ends; cancelling it ends the talk.

        The session's ask lease is free when it returns, raises or is cancelled.
        """
        ...
