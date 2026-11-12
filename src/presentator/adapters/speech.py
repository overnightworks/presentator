"""Speak and hear through the shared speech service, over its pinned client."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final, assert_never

from pydantic import ValidationError
from speech_client import wire
from speech_client.health import UnexpectedStatus, fetch_health
from speech_client.hearing import OPENING_SECONDS, Closed, HearingSession
from speech_client.hearing import Refused as HearingRefused
from speech_client.outcomes import (
    Cut,
    Malformed,
    OverCap,
    PastDeadline,
    Unreachable,
)
from speech_client.relay import (
    HOP_FRAME_BYTES,
    AppEnded,
    BrowserLeft,
    HopClose,
    RelayOutcome,
    RuleBroken,
    relay,
)
from speech_client.speak import Busy, Refused, SpeakOutcome, Spoken, speak
from speech_client.wire import VoiceId

from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
)
from presentator.contracts.voice import SampleLanguage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from speech_client.health import HealthOutcome

    from presentator.ports.copresenter import HearingSocket

_LOGGER = logging.getLogger(__name__)
_HEARING_FAILED_LOG: Final = "hearing failed: %s"

# These bound presentator's own resources and its patience in a live talk: a
# worker thread's life, one sentence's memory. They vary by no deployment.
_SPEAK_DEADLINE_SECONDS: Final = 30.0
_SPEAK_CAP_BYTES: Final = 8 * 1024 * 1024
_HEALTH_DEADLINE_SECONDS: Final = 5.0
_HEALTH_CAP_BYTES: Final = 64 * 1024
# About 43 s of the 24 kHz PCM16 input: what a hearing holds while the service
# opens or stops taking audio.
_HELD_LIMIT_BYTES: Final = 2 * 1024 * 1024
# Past any talk the operator gives.
_HEARING_DEADLINE_SECONDS: Final = 4 * 60 * 60.0


class _WarningsOnly(logging.Filter):
    """Drops the transports' DEBUG and INFO lines, which hold upgrade headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


_TRANSPORT_TRACES: Final = _WarningsOnly()


def silence_transport_traces() -> None:
    """Keep the transports' traces of a call out of every log, for the process.

    httpcore2 traces a response's headers at DEBUG and httpx2 the response line
    at INFO, whatever level an operator set; those carry what the called service
    answered.
    """
    for name in ("httpx2", "httpcore2.http11"):
        logging.getLogger(name).addFilter(_TRANSPORT_TRACES)


def voice_speaks(voice: VoiceId, language: SampleLanguage) -> bool:
    """Whether the service's catalogue has `voice` speak `language`."""
    return _client_language(language) in wire.VOICES[voice].languages


def _client_language(language: SampleLanguage) -> wire.Language:
    return wire.Language(language.value)


def _sample_language(language: wire.Language) -> SampleLanguage:
    return SampleLanguage(language.value)


class SpeechSpeaking:
    """Speak one sentence at the shared speech service."""

    def __init__(
        self,
        origin: str,
        *,
        voices: Mapping[SampleLanguage, VoiceId],
        bounded_speak: Callable[..., SpeakOutcome] = speak,
    ) -> None:
        """Keep the service's origin and the voice of each language."""
        self._origin = origin
        self._voices = voices
        self._bounded_speak = bounded_speak

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        """The sentence as one whole WAV, exactly as the service sent it."""
        spoken = SampleLanguage(language)
        try:
            outcome: SpeakOutcome | None = await asyncio.to_thread(
                self._bounded_speak,
                self._origin,
                text,
                self._voices[spoken],
                language=_client_language(spoken),
                response_format=wire.ResponseFormat.WAV,
                workload_token=tokens.workload,
                person_token=tokens.person,
                deadline_seconds=_SPEAK_DEADLINE_SECONDS,
                cap_bytes=_SPEAK_CAP_BYTES,
            )
        # Start refuses a voice outside the catalogue, and the origin is checked
        # apart, so what the request can still refuse here is the sentence.
        except ValidationError:
            outcome = None
        if outcome is None:
            _LOGGER.warning("speaking refused a sentence the wire cannot carry")
            raise CoPresenterUnavailableError
        return _audio_of(outcome)


def _audio_of(outcome: SpeakOutcome) -> bytes:
    match outcome:
        case Spoken():
            return outcome.audio
        case Refused():
            _LOGGER.warning(
                "speaking refused: status=%d code=%s param=%s",
                outcome.status,
                outcome.code.value,
                outcome.param,
            )
        case Busy():
            _LOGGER.warning("speaking busy: wait=%ds", outcome.wait.retry_after_seconds)
        case Cut() | PastDeadline() | OverCap() | Malformed() | Unreachable():
            _LOGGER.warning("speaking failed: %s", type(outcome).__name__)
        case _:
            assert_never(outcome)
    raise CoPresenterUnavailableError


class SpeechHearing:
    """Read the shared speech service's readiness and hear through it."""

    def __init__(
        self,
        origin: str,
        *,
        voices: Mapping[SampleLanguage, VoiceId],
        bounded_health_fetch: Callable[..., HealthOutcome] = fetch_health,
        hearing_deadline_seconds: float = _HEARING_DEADLINE_SECONDS,
        opening_seconds: float = OPENING_SECONDS,
    ) -> None:
        """Keep the service's origin, the voices readiness asks after and the bounds."""
        self._origin = origin
        self._voices = voices
        self._bounded_health_fetch = bounded_health_fetch
        self._hearing_deadline_seconds = hearing_deadline_seconds
        self._opening_seconds = opening_seconds

    async def readiness(self) -> CoPresenterReadiness:
        """Say whether the service hears, or that a configured voice cannot speak."""
        outcome = await asyncio.to_thread(
            self._bounded_health_fetch,
            self._origin,
            deadline_seconds=_HEALTH_DEADLINE_SECONDS,
            cap_bytes=_HEALTH_CAP_BYTES,
        )
        health = _health_of(outcome)
        if health.hearing.ready and not all(
            _speaks(health, voice) for voice in self._voices.values()
        ):
            _LOGGER.warning("a configured voice cannot speak")
            raise CoPresenterUnavailableError
        return CoPresenterReadiness(
            local_hearing_ready=health.hearing.ready,
        )

    async def relay(
        self,
        browser: HearingSocket,
        language: str,
        tokens: SpeechTokens,
        *,
        ended: asyncio.Event,
        heard: Callable[[HearingTranscript], Awaitable[None]],
    ) -> None:
        """Relay the browser's audio to the service and its transcripts back."""
        try:
            hears = wire.Language(language)
        except ValueError:
            _LOGGER.warning("hearing refused a language the wire does not name")
            await self.refuse(browser)
            return
        session = HearingSession(
            self._origin,
            workload_token=tokens.workload,
            person_token=tokens.person,
            languages=_hearing_languages(hears),
            held_limit_bytes=_HELD_LIMIT_BYTES,
            deadline_seconds=self._hearing_deadline_seconds,
            opening_seconds=self._opening_seconds,
        )
        try:
            outcome = await relay(
                browser,
                session,
                app_ended=ended,
                on_event=_Transcripts(heard).on_event,
                frame_limit_bytes=HOP_FRAME_BYTES,
            )
        except _ServiceFailureError as failure:
            _LOGGER.warning(_HEARING_FAILED_LOG, failure.event_type)
            await self.refuse(browser)
            return
        _log_ending(outcome)

    async def refuse(self, browser: HearingSocket) -> None:
        """Close the browser as a hearing the service cannot give."""
        await browser.close(HopClose.SERVICE_FAILED)


def _health_of(outcome: HealthOutcome) -> wire.Health:
    match outcome:
        case wire.Health():
            return outcome
        case UnexpectedStatus():
            _LOGGER.warning("readiness failed: status=%d", outcome.status)
        case Malformed() | OverCap() | PastDeadline() | Cut() | Unreachable():
            _LOGGER.warning("readiness failed: %s", type(outcome).__name__)
        case _:
            assert_never(outcome)
    raise CoPresenterUnavailableError


def _speaks(health: wire.Health, voice: VoiceId) -> bool:
    offered = health.speaking.voices.get(voice)
    return offered is not None and (offered.ready or health.speaking.ready)


def _hearing_languages(main: wire.Language) -> tuple[wire.Language, ...]:
    """The languages a hearing asks for: the main one first, then the other one."""
    return (main, *(code for code in wire.Language if code is not main))


class _ServiceFailureError(Exception):
    """A failed item or an error event ended the hearing; names only its type."""

    def __init__(self, event_type: str) -> None:
        super().__init__()
        self.event_type = event_type


class _Transcripts:
    """Turn the service's events into the transcripts the browser is shown."""

    def __init__(self, heard: Callable[[HearingTranscript], Awaitable[None]]) -> None:
        self._heard = heard
        self._heard_so_far: dict[str, str] = {}

    async def on_event(self, event: wire.ServerEvent) -> None:
        match event:
            case wire.TranscriptDelta():
                text = self._heard_so_far.get(event.item_id, "") + event.delta
                self._heard_so_far[event.item_id] = text
                await self._heard(HearingTranscript(text=text, final=False))
            case wire.TranscriptCompleted():
                self._heard_so_far.pop(event.item_id, None)
                if event.transcript:
                    (decoded,) = event.languages
                    await self._heard(
                        HearingTranscript(
                            text=event.transcript,
                            final=True,
                            language=_sample_language(decoded.code),
                        )
                    )
            case wire.TranscriptFailed() | wire.ErrorEvent():
                raise _ServiceFailureError(type(event).__name__)
            case _:
                return


def _log_ending(outcome: RelayOutcome) -> None:
    match outcome:
        case Closed() | BrowserLeft() | AppEnded():
            return
        case HearingRefused():
            _LOGGER.warning("hearing failed: Refused status=%d", outcome.status)
        case RuleBroken():
            _LOGGER.warning("hearing failed: RuleBroken close=%d", outcome.close.value)
        case Busy() | OverCap() | Malformed() | PastDeadline() | Cut() | Unreachable():
            _LOGGER.warning(_HEARING_FAILED_LOG, type(outcome).__name__)
        case _:
            assert_never(outcome)
