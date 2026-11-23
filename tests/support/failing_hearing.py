"""A hearing that hands on its lines until one breaks it."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable

    from presentator.contracts.copresenter import (
        CoPresenterReadiness,
        CoPresenterUnavailableError,
        HearingTranscript,
        SpeechTokenSource,
    )
    from presentator.ports.copresenter import HearingSocket
    from presentator.ports.copresenter import SpeechHearing as SpeechHearingPort


class HearingFaultError(Exception):
    """The unexpected failure a hearing that breaks mid-talk lets out."""


class FailingHearing:
    """A hearing that hands on its lines until one breaks it."""

    def __init__(self, speech: SpeechHearingPort, *, on_line: int) -> None:
        """Wrap this hearing; the line numbered `on_line` breaks it."""
        self._speech = speech
        self._on_line = on_line

    async def readiness(self) -> CoPresenterReadiness:
        return await self._speech.readiness()

    async def relay(  # noqa: PLR0913 - the port's signature
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokenSource,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
        text: Callable[[str], None] | None = None,
    ) -> None:
        lines = 0

        async def heard_then_break(transcript: HearingTranscript) -> None:
            nonlocal lines
            lines += transcript.final
            if lines == self._on_line:
                raise HearingFaultError
            await heard(transcript)

        await self._speech.relay(
            browser, language, tokens, ended=ended, heard=heard_then_break, text=text
        )

    async def refuse(
        self, browser: HearingSocket, unavailable: CoPresenterUnavailableError
    ) -> None:
        await self._speech.refuse(browser, unavailable)


def hearing_failing_on(
    hearing: SpeechHearingPort, on_line: int | None
) -> SpeechHearingPort:
    """The hearing itself, or one that breaks on the line numbered `on_line`."""
    return hearing if on_line is None else FailingHearing(hearing, on_line=on_line)
