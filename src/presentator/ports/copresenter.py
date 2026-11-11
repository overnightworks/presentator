"""The typed private capabilities used by the co-presenter application."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from presentator.contracts.copresenter import (
        AccessRefusal,
        AskingPerson,
        CoPresenterReadiness,
        DeckText,
        HearingTranscript,
        ShowSlide,
        SpeechTokens,
    )
    from presentator.contracts.decks import Deck, Source


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

    async def relay(
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokens,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> None:
        """Hear for the person these tokens name until the hearing ends.

        Relays the browser's audio to the service and hands each transcript to
        `heard`. Closes the browser socket with the code of how the hearing
        ended, and ends it at once when `ended` is set. A browser that left,
        or an unexpected failure, passes to the caller.
        """
        ...

    async def refuse(self, browser: HearingSocket) -> None:
        """Close the browser socket as a hearing the service cannot give."""
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
