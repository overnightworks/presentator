"""A fake of the shared speech service, served on a loopback socket the test binds."""

from __future__ import annotations

import asyncio
import json
import re
import socket
import struct
import threading
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from speech_client import wire

from tests.presentator.adapters.support.serving import (
    Serving,
    loopback_listener,
    origin_of,
)
from tests.support.lobby import A_SPEECH_VOICE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Generator

type Answer = Callable[[], Response]
type Hear = Callable[[WebSocket], Awaitable[None]]
type Served = Callable[[], AbstractAsyncContextManager[str]]

PLANTED: Final = "planted-MARKER"
_MARKED_HEADERS: Final = {"X-Marker": PLANTED}
_PAST_THE_SPEAK_CAP: Final = 9 * 1024 * 1024
_PAST_THE_HEALTH_CAP: Final = 70_000
_SECONDS_TO_RETRY: Final = "3"
_HELD_ANSWER_SECONDS: Final = 10


def _riff_chunk(name: bytes, payload: bytes) -> bytes:
    padding = b"\0" if len(payload) % 2 else b""
    return name + struct.pack("<I", len(payload)) + payload + padding


def _a_wav_with_its_ai_mark() -> bytes:
    """A well-formed WAV with a `LIST/INFO/ICMT` chunk, the AI mark's form."""
    pcm_format = struct.pack("<HHIIHH", 1, 1, 24_000, 48_000, 2, 16)
    body = (
        b"WAVE"
        + _riff_chunk(b"fmt ", pcm_format)
        + _riff_chunk(b"LIST", b"INFO" + _riff_chunk(b"ICMT", b"ai-generated"))
        + _riff_chunk(b"data", b"\0\0" * 8)
    )
    return b"RIFF" + struct.pack("<I", len(body)) + body


A_WAV: Final[bytes] = _a_wav_with_its_ai_mark()
AN_ITEM: Final = "item-1"


def a_health_document(
    *,
    hearing_ready: bool = True,
    voice_ready: bool = True,
    speaking_ready: bool = True,
    voice: str = A_SPEECH_VOICE,
    another_ready_voice: str | None = None,
) -> dict[str, object]:
    """A `/health` body; `voice` names what the service offers."""
    voices = {voice: {"model": "m", "ready": voice_ready, "sample_rate": 1}}
    if another_ready_voice is not None:
        voices[another_ready_voice] = {"model": "m", "ready": True, "sample_rate": 1}
    return {
        "speaking": {"ready": speaking_ready, "voices": voices},
        "hearing": {"model": "m", "ready": hearing_ready},
        "card_memory_mb": 1,
    }


def a_refusal(
    code: wire.RefusalCode,
    param: wire.RequestField | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> Answer:
    """A speak refusal in the wire's own form, as the service builds it."""
    refusal = wire.refusal(code, param)
    return lambda: JSONResponse(
        refusal.model_dump(mode="json", exclude={"status"}),
        status_code=refusal.status,
        headers=headers,
    )


def a_marked_refusal(code: wire.RefusalCode) -> Answer:
    """A refusal with the marker as its `message` and in a response header."""
    refusal = wire.refusal(code)
    body = refusal.model_dump(mode="json", exclude={"status"})
    body["error"]["message"] = PLANTED
    return lambda: JSONResponse(
        body, status_code=refusal.status, headers=_MARKED_HEADERS
    )


def a_chunked_wav(audio: bytes = A_WAV) -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield audio

    return StreamingResponse(chunks(), media_type="audio/wav")


def a_held_wav(arrived: threading.Event, release: threading.Event) -> Answer:
    """A WAV that holds its answer until the test releases it."""

    def answer() -> Response:
        async def chunks() -> AsyncIterator[bytes]:
            arrived.set()
            await asyncio.to_thread(release.wait, _HELD_ANSWER_SECONDS)
            yield A_WAV

        return StreamingResponse(chunks(), media_type="audio/wav")

    return answer


def _a_cut_wav() -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield A_WAV
        message = "cut"
        raise RuntimeError(message)

    return StreamingResponse(chunks(), media_type="audio/wav")


def _past_the_speak_cap() -> Response:
    return a_chunked_wav(A_WAV + bytes(_PAST_THE_SPEAK_CAP))


def _a_marked_non_json_500() -> Response:
    return Response(PLANTED.encode(), status_code=500, headers=_MARKED_HEADERS)


SPEAK_FAILURES: Final[dict[str, Answer]] = {
    **{
        code.value: a_refusal(code)
        for code in wire.RefusalCode
        if code is not wire.RefusalCode.BUSY
    },
    "unavailable-voice": a_refusal(wire.RefusalCode.UNAVAILABLE, "voice"),
    "busy-with-a-wait": a_refusal(
        wire.RefusalCode.BUSY, headers={wire.RETRY_AFTER_HEADER: _SECONDS_TO_RETRY}
    ),
    "marked-refusal": a_marked_refusal(wire.RefusalCode.FORBIDDEN),
    "non-json-500": _a_marked_non_json_500,
    "empty-body": lambda: StreamingResponse(iter(()), media_type="audio/wav"),
    "cut-body": _a_cut_wav,
    "past-the-cap": _past_the_speak_cap,
}


def _a_health_body(**changes: object) -> Answer:
    return lambda: JSONResponse({**a_health_document(), **changes})


def _a_cut_health_body() -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"speaking":'
        message = "cut"
        raise RuntimeError(message)

    return StreamingResponse(chunks(), media_type="application/json")


def _the_v0_1_shape() -> Response:
    return JSONResponse(
        {
            "speaking": {"model": "m", "ready": True, "sample_rate": 22050},
            "hearing": {"model": "m", "ready": True},
            "sample_rate": 16000,
            "card_memory_mb": 1,
        }
    )


def _a_service_in_its_start_load() -> Response:
    return JSONResponse(
        a_health_document(
            voice_ready=False,
            speaking_ready=False,
            another_ready_voice="another-voice",
        )
    )


def _sample_rate_as_text() -> Response:
    voices = {A_SPEECH_VOICE: {"model": "m", "ready": True, "sample_rate": "1"}}
    return _a_health_body(speaking={"ready": True, "voices": voices})()


HEALTH_FAILURES: Final[dict[str, Answer]] = {
    "non-200-marked": lambda: JSONResponse(
        {"message": PLANTED}, status_code=503, headers=_MARKED_HEADERS
    ),
    "garbage-marked": lambda: Response(
        PLANTED.encode(), media_type="application/json", headers=_MARKED_HEADERS
    ),
    "missing-section": _a_health_body(hearing=None),
    "ready-as-text": _a_health_body(hearing={"model": "m", "ready": "true"}),
    "ready-as-number": _a_health_body(hearing={"model": "m", "ready": 1}),
    "sample-rate-as-text": _sample_rate_as_text,
    "past-the-cap": lambda: JSONResponse(
        a_health_document(another_ready_voice="v" * _PAST_THE_HEALTH_CAP)
    ),
    "cut-body": _a_cut_health_body,
    "v0.1-shape": _the_v0_1_shape,
    "voice-absent": lambda: JSONResponse(a_health_document(voice="another-voice")),
    "service-in-its-start-load": _a_service_in_its_start_load,
}


async def accept_and_hold(socket_to_browser: WebSocket) -> None:
    await socket_to_browser.accept()
    await socket_to_browser.receive()


def a_delta(text: str, item: str = AN_ITEM) -> wire.TranscriptDelta:
    return wire.TranscriptDelta(item_id=item, delta=text)


def a_completed(text: str, item: str = AN_ITEM) -> wire.TranscriptCompleted:
    return wire.TranscriptCompleted(
        item_id=item, transcript=text, usage=wire.DurationUsage(seconds=1.0)
    )


def an_error_event() -> wire.ErrorEvent:
    return wire.error_event(wire.RefusalCode.UNAVAILABLE)


def a_failed_item() -> wire.TranscriptFailed:
    return wire.TranscriptFailed(
        item_id=AN_ITEM, error=wire.item_failure(wire.RefusalCode.UNAVAILABLE)
    )


def a_marked_error_event() -> wire.ErrorEvent:
    return wire.ErrorEvent(
        error=wire.RealtimeError(
            message=PLANTED,
            type="server_error",
            code=wire.RefusalCode.UNAVAILABLE,
            param=None,
            event_id=None,
        )
    )


def a_marked_failed_item() -> wire.TranscriptFailed:
    return wire.TranscriptFailed(
        item_id=AN_ITEM,
        error=wire.ItemFailure(
            message=PLANTED, type="server_error", code=wire.RefusalCode.UNAVAILABLE
        ),
    )


def _encoded(event: wire.ServerEvent | str) -> str:
    return event if isinstance(event, str) else event.model_dump_json()


def sending(*events: wire.ServerEvent) -> Hear:
    """Accept, send the events at once, then close."""

    async def realtime(socket_to_browser: WebSocket) -> None:
        await socket_to_browser.accept()
        for event in events:
            await socket_to_browser.send_text(event.model_dump_json())
        await socket_to_browser.close()

    return realtime


def after_the_first_audio(
    *events: wire.ServerEvent | str, close_code: int = 1000, close_reason: str = ""
) -> Hear:
    """Accept, take the `session.update` and one append, send the events, close."""

    async def realtime(socket_to_browser: WebSocket) -> None:
        await socket_to_browser.accept()
        await socket_to_browser.receive()
        await socket_to_browser.receive()
        for event in events:
            await socket_to_browser.send_text(_encoded(event))
        await socket_to_browser.close(code=close_code, reason=close_reason)

    return realtime


def crashing_after_the_first_audio() -> Hear:
    """Accept, take the first audio, then the handler dies."""

    async def realtime(socket_to_browser: WebSocket) -> None:
        await socket_to_browser.accept()
        await socket_to_browser.receive()
        await socket_to_browser.receive()
        message = "the service crashed"
        raise RuntimeError(message)

    return realtime


def denying(
    status: int, *, headers: dict[str, str] | None = None, marked: bool = False
) -> Hear:
    """Refuse the upgrade; `marked` puts the marker in its body and a header."""

    async def realtime(socket_to_browser: WebSocket) -> None:
        await socket_to_browser.send_denial_response(
            Response(
                content=PLANTED if marked else None,
                status_code=status,
                headers={**(_MARKED_HEADERS if marked else {}), **(headers or {})},
            )
        )

    return realtime


def busy_at_the_upgrade(*, marked: bool = False) -> Hear:
    return denying(
        429, headers={wire.RETRY_AFTER_HEADER: _SECONDS_TO_RETRY}, marked=marked
    )


def reading(service: FakeSpeechService) -> Hear:
    """A Realtime handler that accepts and keeps every client event."""

    async def realtime(socket_to_browser: WebSocket) -> None:
        await socket_to_browser.accept()
        service.connected.set()
        await service.read_events(socket_to_browser)

    return realtime


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """One request the fake saw: its headers and its body."""

    headers: dict[str, str]
    body: bytes = b""


@dataclass(slots=True)
class FakeSpeechService:
    """The service's three endpoints, each answering what a scenario chose."""

    speak_answers: list[Answer] = field(default_factory=list[Answer])
    health_answer: Answer = lambda: JSONResponse(a_health_document())
    realtime: Hear = accept_and_hold
    speak_requests: list[RecordedRequest] = field(default_factory=list[RecordedRequest])
    health_requests: list[RecordedRequest] = field(
        default_factory=list[RecordedRequest]
    )
    upgrades: list[RecordedRequest] = field(default_factory=list[RecordedRequest])
    events: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    closes: list[str] = field(default_factory=list[str])
    upgrade_paths: list[str] = field(default_factory=list[str])
    arrived: threading.Semaphore = field(default_factory=lambda: threading.Semaphore(0))
    connected: threading.Event = field(default_factory=threading.Event)
    closed: threading.Event = field(default_factory=threading.Event)

    def app(self) -> FastAPI:
        """The fake as an ASGI app."""
        app = FastAPI()
        app.add_api_route(wire.HEALTH_PATH, self._health, methods=["GET"])
        app.add_api_route(wire.SPEECH_PATH, self._speak, methods=["POST"])
        app.add_api_websocket_route(wire.REALTIME_PATH, self._realtime)
        return app

    async def _health(self, request: Request) -> Response:
        self.health_requests.append(RecordedRequest(dict(request.headers)))
        return self.health_answer()

    async def _speak(self, request: Request) -> Response:
        self.speak_requests.append(
            RecordedRequest(dict(request.headers), await request.body())
        )
        if not self.speak_answers:
            return a_chunked_wav()
        return self.speak_answers.pop(0)()

    async def _realtime(self, connection: WebSocket) -> None:
        self.upgrades.append(RecordedRequest(dict(connection.headers)))
        self.upgrade_paths.append(str(connection.url.query))
        await self.realtime(connection)

    async def read_events(self, connection: WebSocket) -> None:
        """Keep every client event until the peer leaves."""
        while True:
            message = await connection.receive()
            if message["type"] == "websocket.disconnect":
                self.closes.append("closed")
                self.closed.set()
                return
            self.events.append(json.loads(message["text"]))
            self.arrived.release()


@asynccontextmanager
async def a_loopback_server(app: FastAPI) -> AsyncGenerator[str]:
    """Serve `app` on a loopback port for the block; yield its origin."""
    listener = loopback_listener()
    origin = origin_of(listener)
    async with Serving(app, listener):
        yield origin


@contextmanager
def a_served_origin(serving: Served) -> Generator[str]:
    """Run `serving` from a thread of its own, for a caller with a loop of its own."""
    ready = threading.Event()
    served: list[str] = []
    failed: list[BaseException] = []
    stops: list[Callable[[], object]] = []

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        stops.append(lambda: loop.call_soon_threadsafe(stop.set))
        async with serving() as origin:
            served.append(origin)
            ready.set()
            await stop.wait()

    def run() -> None:
        try:
            asyncio.run(serve())
        except BaseException as error:
            failed.append(error)
            ready.set()
            raise

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    ready.wait(10)
    try:
        assert served, failed
        yield served[0]
    finally:
        stops[0]()
        thread.join(10)


def _content_length_of(head: bytes) -> int:
    matched = re.search(rb"content-length: *(\d+)", head, re.IGNORECASE)
    return int(matched[1]) if matched else 0


@dataclass(frozen=True, slots=True)
class RawPeer:
    """Answers whatever bytes a scenario gives on every connection, then closes."""

    answer: bytes

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # The whole request is read first, so closing cannot reset the client's
        # socket before it has taken the answer.
        head = await reader.readuntil(b"\r\n\r\n")
        await reader.readexactly(_content_length_of(head))
        writer.write(self.answer)
        await writer.drain()
        writer.close()

    @asynccontextmanager
    async def serving(self) -> AsyncGenerator[str]:
        """Serve for the block; yield the origin a client reaches it at."""
        listener = loopback_listener()
        origin = origin_of(listener)
        listener.listen()
        listener.settimeout(0.0)
        server = await asyncio.start_server(self._serve, sock=listener)
        async with server:
            yield origin


@asynccontextmanager
async def nobody_listens() -> AsyncGenerator[str]:
    """Yield an origin nothing listens on, in a server's place."""
    yield a_closed_port_origin()


MALFORMED_PEERS: Final[dict[str, bytes]] = {
    "status-line-not-http": f"NOT-HTTP {PLANTED}\r\n\r\n".encode(),
    "cut-header-block": f"HTTP/1.1 200 OK\r\nX-Marker: {PLANTED}".encode(),
    "marked-status-reason": (
        f"HTTP/1.1 500 {PLANTED}\r\nX-Marker: {PLANTED}\r\n"
        f"Content-Length: {len(PLANTED)}\r\n\r\n{PLANTED}"
    ).encode(),
}


def a_closed_port_origin() -> str:
    """An origin nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
    return f"http://{host}:{port}"
