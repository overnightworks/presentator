"""Speak and hear through the shared speech service, over the client's wire."""

from __future__ import annotations

import base64
import json
import logging
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import TYPE_CHECKING, Final, cast

import httpx2
from httpx2.websockets import (
    AsyncWebSocketSession,
    HTTPXWSException,
    WebSocketDisconnect,
    WebSocketUpgradeError,
)
from pydantic import BaseModel, ValidationError
from speech_runtime_client import wire

from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
    HearingTranscript,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from presentator.contracts.copresenter import SpeechTokens
    from presentator.ports.copresenter import PrivateHearing

_LOGGER = logging.getLogger(__name__)
_STEP_TIMEOUT: Final = 5.0
_SPEAK_READ_TIMEOUT: Final = 30.0
_EVENTS_THAT_MATTER: Final[dict[str, type[BaseModel]]] = {
    wire.TranscriptDelta.model_fields["type"].get_default(): wire.TranscriptDelta,
    wire.TranscriptCompleted.model_fields["type"].get_default(): (
        wire.TranscriptCompleted
    ),
    wire.TranscriptFailed.model_fields["type"].get_default(): wire.TranscriptFailed,
    wire.ErrorEvent.model_fields["type"].get_default(): wire.ErrorEvent,
}


type TransportFactory = Callable[[], httpx2.AsyncBaseTransport]


def _client(
    origin: str, timeout: httpx2.Timeout, transports: TransportFactory
) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=origin,
        transport=transports(),
        timeout=timeout,
        trust_env=False,
        follow_redirects=False,
    )


def _unavailable(**closed: object) -> CoPresenterUnavailableError:
    # Only closed values are shown: a body, a message or an exception may carry
    # a word of the person or a token.
    _LOGGER.warning(
        "speech unavailable: %s", " ".join(f"{k}={v}" for k, v in closed.items())
    )
    return CoPresenterUnavailableError()


def _busy_seconds(status: int, headers: httpx2.Headers) -> object:
    try:
        wait = wire.busy_wait(status, headers)
    except wire.RetryAfterError:
        return "malformed"
    return None if wait is None else wait.retry_after_seconds


def _refusal_of(response: httpx2.Response) -> CoPresenterUnavailableError:
    status = response.status_code
    wait = _busy_seconds(status, response.headers)
    try:
        detail = wire.Refusal.model_validate_json(response.content).error
    except ValidationError:
        return _unavailable(status=status, refusal="unreadable", wait=wait)
    return _unavailable(
        status=status, code=detail.code.value, param=detail.param, wait=wait
    )


class SpeechSpeaking:
    """Speak one sentence through the shared speech service."""

    def __init__(
        self,
        origin: str,
        voice: str,
        *,
        transports: TransportFactory = httpx2.AsyncHTTPTransport,
        read_timeout: float = _SPEAK_READ_TIMEOUT,
    ) -> None:
        """Keep the service's origin, the one voice and how long it may stay silent."""
        self._origin = origin
        self._voice = voice
        self._transports = transports
        self._timeout = httpx2.Timeout(_STEP_TIMEOUT, read=read_timeout)

    async def speak(self, text: str, language: str, tokens: SpeechTokens) -> bytes:
        """Read the whole WAV, or close the connection and raise."""
        # The wire speaks German only, so the sentence's language is not sent.
        del language
        try:
            body = wire.speech_request_body(text, self._voice)
        except ValidationError:
            raise _unavailable(request="not speakable") from None
        headers = wire.speech_request_headers(
            workload_token=tokens.workload, person_token=tokens.person
        )
        refused: CoPresenterUnavailableError | None = None
        try:
            async with (
                _client(self._origin, self._timeout, self._transports) as client,
                client.stream(
                    "POST", wire.SPEECH_PATH, content=body, headers=headers
                ) as response,
            ):
                if response.status_code == HTTPStatus.OK:
                    audio = b"".join([chunk async for chunk in response.aiter_bytes()])
                else:
                    await response.aread()
                    audio = b""
                    refused = _refusal_of(response)
        except httpx2.HTTPError as failed:
            raise _unavailable(failure=type(failed).__name__) from None
        # Raised after the client closed, so a transport that wraps what leaves
        # it in a group cannot hide the refusal.
        if refused is not None:
            raise refused
        if not audio:
            raise _unavailable(audio="empty")
        return audio


class SpeechHearing:
    """Read the service's readiness and hear through its Realtime transcription."""

    def __init__(
        self,
        origin: str,
        voice: str,
        *,
        transports: TransportFactory = httpx2.AsyncHTTPTransport,
    ) -> None:
        """Keep the service's origin, the voice asked about and how to reach it."""
        self._origin = origin
        self._voice = voice
        self._transports = transports

    def _client(self) -> httpx2.AsyncClient:
        return _client(self._origin, httpx2.Timeout(_STEP_TIMEOUT), self._transports)

    async def readiness(self) -> CoPresenterReadiness:
        """The hearing's state and rate; unavailable while the voice cannot speak."""
        try:
            async with self._client() as client:
                response = await client.get(wire.HEALTH_PATH)
            if response.status_code != HTTPStatus.OK:
                raise _unavailable(status=response.status_code)
            health = wire.Health.model_validate_json(response.content)
        except httpx2.HTTPError as refused:
            raise _unavailable(failure=type(refused).__name__) from None
        except ValidationError:
            raise _unavailable(health="unreadable") from None
        rate = wire.SessionInput().format.rate
        if not health.hearing.ready:
            return CoPresenterReadiness(
                hearing_sample_rate=rate, local_hearing_ready=False
            )
        voice = health.speaking.voices.get(self._voice)
        if voice is None or not voice.ready:
            raise _unavailable(voice="not ready")
        return CoPresenterReadiness(hearing_sample_rate=rate, local_hearing_ready=True)

    @asynccontextmanager
    async def hear(
        self, language: str, tokens: SpeechTokens
    ) -> AsyncGenerator[PrivateHearing]:
        """Open one Realtime transcription session for the person's speech."""
        try:
            setup = _session_update(language)
        except ValueError:
            raise _unavailable(language="not heard") from None
        headers = wire.speech_request_headers(
            workload_token=tokens.workload, person_token=tokens.person
        )
        del headers[wire.CONTENT_TYPE_HEADER]
        try:
            async with (
                self._client() as client,
                client.websocket(
                    wire.REALTIME_PATH,
                    params={wire.REALTIME_INTENT_PARAM: wire.REALTIME_INTENT},
                    headers=headers,
                ) as socket,
            ):
                await socket.send_text(setup)
                yield _RealtimeHearing(socket)
        except WebSocketUpgradeError as refused:
            status = refused.response.status_code
            raise _unavailable(
                upgrade=status, wait=_busy_seconds(status, refused.response.headers)
            ) from None
        except BaseExceptionGroup as group:
            _, caller = group.split(_EXPECTED)
            if caller is not None:
                raise
            raise _unavailable(session="failed") from None
        except _EXPECTED as refused:
            raise _unavailable(failure=type(refused).__name__) from None


_EXPECTED: Final = (CoPresenterUnavailableError, httpx2.HTTPError, HTTPXWSException)


def _session_update(language: str) -> str:
    setting = wire.TranscriptionSetting(language=wire.HearingLanguage(language))
    session = wire.TranscriptionSession(
        type="transcription",
        audio=wire.SessionAudio(input=wire.SessionInput(transcription=setting)),
    )
    update = wire.SessionUpdate(type="session.update", session=session)
    return update.model_dump_json(exclude_none=True)


class _RealtimeHearing:
    def __init__(self, socket: AsyncWebSocketSession) -> None:
        self._socket = socket
        self._heard: dict[str, str] = {}

    async def send_pcm(self, frame: bytes) -> None:
        append = wire.AudioAppend(
            type="input_audio_buffer.append",
            audio=base64.b64encode(frame).decode("ascii"),
        )
        await self._socket.send_text(append.model_dump_json(exclude_none=True))

    async def receive(self) -> HearingTranscript | None:
        while True:
            try:
                event = _event_of(await self._socket.receive_text())
            except WebSocketDisconnect:
                return None
            except HTTPXWSException as refused:
                raise _unavailable(failure=type(refused).__name__) from None
            transcript = self._transcript_of(event)
            if transcript is not None:
                return transcript

    def _transcript_of(self, event: BaseModel | None) -> HearingTranscript | None:
        match event:
            case wire.TranscriptDelta(item_id=item, delta=delta):
                heard = self._heard.get(item, "") + delta
                self._heard[item] = heard
                return HearingTranscript(text=heard, final=False)
            case wire.TranscriptCompleted(item_id=item, transcript=text):
                self._heard.pop(item, None)
                return HearingTranscript(text=text, final=True) if text else None
            case wire.TranscriptFailed() | wire.ErrorEvent():
                raise _unavailable(event=event.type)
            case _:
                return None

    async def aclose(self) -> None:
        await self._socket.close()


def _event_of(frame: str) -> BaseModel | None:
    try:
        payload: object = json.loads(frame)
    except ValueError:
        raise _unavailable(frame="not json") from None
    if not isinstance(payload, dict):
        raise _unavailable(frame="not an object")
    kind = str(cast("dict[str, object]", payload).get("type"))
    model = _EVENTS_THAT_MATTER.get(kind)
    if model is None:
        return None
    try:
        return model.model_validate(payload)
    except ValidationError:
        raise _unavailable(event=kind) from None
