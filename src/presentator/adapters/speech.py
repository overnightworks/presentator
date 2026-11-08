"""Speak and hear through the shared speech service, over its pinned client."""

from __future__ import annotations

import asyncio
import logging
from base64 import b64encode
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Final, assert_never, cast

import httpx2
from httpx2.websockets import (
    AsyncWebSocketSession,
    HTTPXWSException,
    WebSocketDisconnect,
    WebSocketUpgradeError,
)
from pydantic import ValidationError
from speech_runtime_client import wire
from speech_runtime_client.health import UnexpectedStatus, fetch_health
from speech_runtime_client.outcomes import (
    Cut,
    Malformed,
    OverCap,
    PastDeadline,
    Unreachable,
)
from speech_runtime_client.speak import Busy, Refused, SpeakOutcome, Spoken, speak
from wsproto.utilities import LocalProtocolError

from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from speech_runtime_client.health import HealthOutcome

    from presentator.ports.copresenter import PrivateHearing

_LOGGER = logging.getLogger(__name__)

# These bound presentator's own resources and its patience in a live talk: a
# worker thread's life, one sentence's memory. They vary by no deployment.
_SPEAK_DEADLINE_SECONDS: Final = 30.0
_SPEAK_CAP_BYTES: Final = 8 * 1024 * 1024
_HEALTH_DEADLINE_SECONDS: Final = 5.0
_HEALTH_CAP_BYTES: Final = 64 * 1024
_UPGRADE_STEP_SECONDS: Final = 5.0


class _WarningsOnly(logging.Filter):
    """Drops the transports' DEBUG and INFO lines, which hold upgrade headers."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno >= logging.WARNING


_TRANSPORT_TRACES: Final = _WarningsOnly()


class SpeechSpeaking:
    """Speak one sentence at the shared speech service."""

    def __init__(
        self,
        origin: str,
        *,
        voice: str,
        bounded_speak: Callable[..., SpeakOutcome] = speak,
    ) -> None:
        """Keep the service's origin and the voice this instance speaks in."""
        self._origin = origin
        self._voice = voice
        self._bounded_speak = bounded_speak

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        """The sentence as one whole WAV, exactly as the service sent it."""
        # The wire takes no language: it speaks one.
        del language
        try:
            outcome: SpeakOutcome | None = await asyncio.to_thread(
                self._bounded_speak,
                self._origin,
                text,
                self._voice,
                response_format=wire.ResponseFormat.WAV,
                workload_token=tokens.workload,
                person_token=tokens.person,
                deadline_seconds=_SPEAK_DEADLINE_SECONDS,
                cap_bytes=_SPEAK_CAP_BYTES,
            )
        # Start refuses an empty voice, and the origin is checked apart, so what
        # the request can still refuse here is the sentence.
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
        voice: str,
        bounded_health_fetch: Callable[..., HealthOutcome] = fetch_health,
    ) -> None:
        """Keep the service's origin and the voice readiness asks after."""
        self._origin = origin
        self._voice = voice
        self._bounded_health_fetch = bounded_health_fetch
        # httpcore2 traces the upgrade's response headers at DEBUG and httpx2 the
        # response line at INFO, whatever level an operator set; those carry
        # what the speech service answered.
        for name in ("httpx2", "httpcore2.http11"):
            logging.getLogger(name).addFilter(_TRANSPORT_TRACES)

    async def readiness(self) -> CoPresenterReadiness:
        """Say whether the service hears, or that its voice is missing."""
        outcome = await asyncio.to_thread(
            self._bounded_health_fetch,
            self._origin,
            deadline_seconds=_HEALTH_DEADLINE_SECONDS,
            cap_bytes=_HEALTH_CAP_BYTES,
        )
        health = _health_of(outcome)
        if health.hearing.ready and not _speaks(health, self._voice):
            _LOGGER.warning("the configured voice cannot speak")
            raise CoPresenterUnavailableError
        return CoPresenterReadiness(
            hearing_sample_rate=wire.SessionInput().format.rate,
            local_hearing_ready=health.hearing.ready,
        )

    @asynccontextmanager
    async def hear(
        self, language: str, tokens: SpeechTokens
    ) -> AsyncGenerator[PrivateHearing]:
        """Open one Realtime connection and name the language it hears."""
        setup = _session_update(language)
        failure: str | None = None
        try:
            async with (
                httpx2.AsyncClient(
                    base_url=self._origin,
                    timeout=httpx2.Timeout(_UPGRADE_STEP_SECONDS),
                    trust_env=False,
                ) as client,
                client.websocket(
                    wire.REALTIME_PATH,
                    params={wire.REALTIME_INTENT_PARAM: wire.REALTIME_INTENT},
                    headers=wire.token_headers(
                        workload_token=tokens.workload, person_token=tokens.person
                    ),
                ) as socket,
            ):
                hearing = _RealtimeHearing(socket)
                await hearing.send(setup)
                yield hearing
        except BaseExceptionGroup as refused:
            if not _only_service_failures(refused):
                raise
            failure = type(refused).__name__
        except WebSocketUpgradeError as refused:
            failure = f"upgrade status {refused.response.status_code}"
        except (httpx2.HTTPError, HTTPXWSException) as refused:
            failure = type(refused).__name__
        if failure is not None:
            _LOGGER.warning("hearing failed: %s", failure)
            raise CoPresenterUnavailableError


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


def _speaks(health: wire.Health, voice: str) -> bool:
    offered = health.speaking.voices.get(voice)
    return offered is not None and offered.ready


def _session_update(language: str) -> wire.SessionUpdate:
    try:
        hears: wire.HearingLanguage | None = wire.HearingLanguage(language)
    except ValueError:
        hears = None
    if hears is None:
        _LOGGER.warning("hearing refused a language the wire does not name")
        raise CoPresenterUnavailableError
    return wire.SessionUpdate(
        type="session.update",
        session=wire.TranscriptionSession(
            type="transcription",
            audio=wire.SessionAudio(
                input=wire.SessionInput(
                    transcription=wire.TranscriptionSetting(language=hears)
                )
            ),
        ),
    )


def _only_service_failures(refused: BaseException) -> bool:
    if isinstance(refused, BaseExceptionGroup):
        group = cast("BaseExceptionGroup[BaseException]", refused)
        return all(_only_service_failures(error) for error in group.exceptions)
    return isinstance(
        refused,
        (CoPresenterUnavailableError, httpx2.HTTPError, HTTPXWSException),
    )


class _RealtimeHearing:
    def __init__(self, socket: AsyncWebSocketSession) -> None:
        self._socket = socket
        self._heard_so_far: dict[str, str] = {}

    async def send(self, event: wire.SessionUpdate | wire.AudioAppend) -> None:
        failure: str | None = None
        try:
            await self._socket.send_text(event.model_dump_json(exclude_none=True))
        # A service that already closed its side makes the library refuse the send.
        except (HTTPXWSException, LocalProtocolError) as refused:
            failure = type(refused).__name__
        if failure is not None:
            _LOGGER.warning("hearing failed: %s", failure)
            raise CoPresenterUnavailableError

    async def send_pcm(self, frame: bytes) -> None:
        await self.send(
            wire.AudioAppend(
                type="input_audio_buffer.append",
                audio=b64encode(frame).decode("ascii"),
            )
        )

    async def receive(self) -> HearingTranscript | None:
        while True:
            event = await self._next_event()
            match event:
                case None:
                    return None
                case wire.TranscriptDelta():
                    heard = self._heard_so_far.get(event.item_id, "") + event.delta
                    self._heard_so_far[event.item_id] = heard
                    return HearingTranscript(text=heard, final=False)
                case wire.TranscriptCompleted():
                    self._heard_so_far.pop(event.item_id, None)
                    if event.transcript:
                        return HearingTranscript(text=event.transcript, final=True)
                case wire.TranscriptFailed() | wire.ErrorEvent():
                    _LOGGER.warning("hearing failed: %s", type(event).__name__)
                    raise CoPresenterUnavailableError
                case _:
                    continue

    async def _next_event(self) -> wire.ServerEvent | None:
        failure: str | None = None
        try:
            return wire.parse_server_event(await self._socket.receive_text())
        except WebSocketDisconnect:
            return None
        except (HTTPXWSException, wire.ServerEventError) as refused:
            failure = type(refused).__name__
        _LOGGER.warning("hearing failed: %s", failure)
        raise CoPresenterUnavailableError

    async def aclose(self) -> None:
        await self._socket.close()
