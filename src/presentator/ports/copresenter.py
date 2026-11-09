"""The typed private capabilities used by the co-presenter application."""

from __future__ import annotations

from collections.abc import Callable
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


class PrivateHearing(Protocol):
    """One private speech-recognition connection."""

    async def send_pcm(self, frame: bytes) -> None:
        """Send one PCM frame upward."""

    async def receive(self) -> HearingTranscript | None:
        """Receive one transcript, or None when the private end closed."""

    async def aclose(self) -> None:
        """Close the connection and await its owned work."""


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

    def hear(
        self, language: str, tokens: SpeechTokens
    ) -> AbstractAsyncContextManager[PrivateHearing]:
        """Open one hearing connection for the person these tokens name."""
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
