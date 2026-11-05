"""The speech service as a test deploys it: its settings values and, below, its fake.

The fake is an ASGI app behind httpx2's in-process transports, so a test reaches it
through the real adapters with no port, no socket and no sleep.
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Final

import httpx2
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from httpx2.websockets import ASGIWebSocketTransport
from speech_runtime_client import wire

A_SPEECH_ORIGIN: Final = "http://speech.test:8080"
A_SPEECH_SCOPE: Final = "a-speech-audience"
A_SPEECH_VOICE: Final = "a-voice"
# A RIFF/WAVE header with the AI mark's LIST/INFO/ICMT chunk the service plants.
DELTA: Final = "conversation.item.input_audio_transcription.delta"
COMPLETED: Final = "conversation.item.input_audio_transcription.completed"
FAILED: Final = "conversation.item.input_audio_transcription.failed"
A_MARKED_WAV: Final = (
    b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00LIST\x0e\x00\x00\x00INFOICMT\x02\x00"
    b"\x00\x00ai\x00\x00data\x04\x00\x00\x00\x01\x02\x03\x04"
)


def a_health(
    *,
    voice: str = A_SPEECH_VOICE,
    voice_ready: bool = True,
    hearing_ready: bool = True,
) -> dict[str, object]:
    """The body `/health` answers at the pinned client's shape."""
    return {
        "speaking": {
            "ready": True,
            "voices": {
                voice: {
                    "model": "a-model",
                    "ready": voice_ready,
                    "sample_rate": 22_050,
                }
            },
        },
        "hearing": {"model": "a-hearing-model", "ready": hearing_ready},
        "card_memory_mb": 0,
    }


@dataclass
class Received:
    """What reached the fake: the headers, the query and the bodies."""

    headers: dict[str, str]
    query: dict[str, str] = field(default_factory=dict[str, str])
    body: bytes = b""


@dataclass
class FakeSpeech:
    """The speech service's three doors and what each one saw."""

    wav: bytes = A_MARKED_WAV
    speak_refusal: Response | None = None
    speak_body: AsyncIterator[bytes] | None = None
    health_answer: Response = field(default_factory=lambda: JSONResponse(a_health()))
    upgrade_refusal: Response | None = None
    after_audio: list[object] = field(default_factory=list[object])
    closes_with: int | None = None
    crashes: bool = False
    max_hearing_sessions: int = 1
    speaks: list[Received] = field(default_factory=list[Received])
    healths: list[Received] = field(default_factory=list[Received])
    upgrades: list[Received] = field(default_factory=list[Received])
    sent: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    open_sessions: int = 0
    client_closed: asyncio.Event | None = None
    session_open: threading.Event = field(default_factory=threading.Event)

    @property
    def events(self) -> list[object]:
        """The types of the events the client sent."""
        return [event["type"] for event in self.sent]

    def app(self) -> FastAPI:
        app = FastAPI()
        app.add_api_route(wire.SPEECH_PATH, self._speak, methods=["POST"])
        app.add_api_route(wire.HEALTH_PATH, self._health, methods=["GET"])
        app.add_api_websocket_route(wire.REALTIME_PATH, self._realtime)
        return app

    def transports(self) -> httpx2.AsyncBaseTransport:
        """A transport into this service; every client takes its own."""
        return ASGIWebSocketTransport(self.app())

    async def _speak(self, request: Request) -> Response:
        self.speaks.append(Received(dict(request.headers), body=await request.body()))
        if self.speak_refusal is not None:
            return self.speak_refusal
        return StreamingResponse(
            self.speak_body or _chunks(self.wav), media_type="audio/wav"
        )

    async def _health(self, request: Request) -> Response:
        self.healths.append(Received(dict(request.headers)))
        return self.health_answer

    async def _realtime(self, socket: WebSocket) -> None:
        self.upgrades.append(
            Received(dict(socket.headers), query=dict(socket.query_params))
        )
        if (
            self.upgrade_refusal is None
            and self.open_sessions >= self.max_hearing_sessions
        ):
            self.upgrade_refusal = a_refusal(wire.RefusalCode.BUSY, retry_after="3")
        if self.upgrade_refusal is not None:
            await socket.send_denial_response(self.upgrade_refusal)
            return
        await socket.accept()
        self.open_sessions += 1
        self.session_open.set()
        try:
            await self._session(socket)
        finally:
            self.open_sessions -= 1

    async def _session(self, socket: WebSocket) -> None:
        try:
            while True:
                event = json.loads(await socket.receive_text())
                self.sent.append(event)
                if event["type"] == "input_audio_buffer.append" and self.after_audio:
                    await self._play(socket)
                    return
        except WebSocketDisconnect:
            if self.client_closed is not None:
                self.client_closed.set()

    async def _play(self, socket: WebSocket) -> None:
        for event in self.after_audio:
            if isinstance(event, bytes):
                await socket.send_bytes(event)
            elif isinstance(event, str):
                await socket.send_text(event)
            else:
                await socket.send_json(event)
        if self.crashes:
            message = "the service crashed"
            raise RuntimeError(message)
        if self.closes_with is not None:
            await socket.close(code=self.closes_with)


async def _chunks(wav: bytes) -> AsyncIterator[bytes]:
    yield wav


def a_refusal(
    code: wire.RefusalCode,
    param: wire.RequestField | None = None,
    *,
    retry_after: str | None = None,
    message: str | None = None,
) -> Response:
    """The service's refusal for `code`, with the status the client's table gives."""
    body = wire.refusal(code, param).model_dump(mode="json")
    if message is not None:
        body["error"]["message"] = message
    headers: Mapping[str, str] = (
        {} if retry_after is None else {wire.RETRY_AFTER_HEADER: retry_after}
    )
    return JSONResponse(
        body, status_code=wire.REFUSAL_STATUS[code], headers=dict(headers)
    )


class _BodyThatBreaks(httpx2.AsyncByteStream):
    def __init__(self, first: bytes, error: Exception | None) -> None:
        self.first = first
        self.error = error
        self.closed = asyncio.Event()
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self.first
        self.started.set()
        if self.error is not None:
            raise self.error
        await asyncio.Future()

    async def aclose(self) -> None:
        self.closed.set()


class BreakingTransport(httpx2.AsyncBaseTransport):
    """A transport whose 200 answer breaks after its first bytes.

    With no `error` the body stalls until the connection is closed, which is what a
    real transport's read timeout or a cancelled caller ends.
    """

    def __init__(self, error: Exception | None) -> None:
        """Break with this error, or stall when there is none."""
        self.body = _BodyThatBreaks(b"RIFF", error)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, stream=self.body)


def delta(text: str, item: str = "item-1") -> dict[str, object]:
    return {"type": DELTA, "item_id": item, "content_index": 0, "delta": text}


def completed(text: str, item: str = "item-1") -> dict[str, object]:
    return {
        "type": COMPLETED,
        "item_id": item,
        "content_index": 0,
        "transcript": text,
        "usage": {"type": "duration", "seconds": 1.0},
    }


def failure(item: str = "item-1", message: str = "said-MARKER") -> dict[str, object]:
    error = {"message": message, "type": "server_error", "code": "unavailable"}
    return {"type": FAILED, "item_id": item, "content_index": 0, "error": error}


def error_event(message: str = "said-MARKER") -> dict[str, object]:
    error = {"message": message, "type": "server_error", "code": "unavailable"}
    return {"type": "error", "error": error}
