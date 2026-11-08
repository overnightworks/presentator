"""A fake of the shared speech service, served on a loopback socket the test binds."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from speech_runtime_client import wire

from tests.presentator.adapters.support.serving import (
    Serving,
    loopback_listener,
    origin_of,
)
from tests.support.lobby import A_SPEECH_VOICE

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Generator

A_WAV: bytes = b"RIFF\x00\x00\x00\x00WAVEfmt-LIST-INFO-ICMT-ai-generated"
type Answer = Callable[[], Response]
type Hear = Callable[[WebSocket], Awaitable[None]]


def a_health_document(
    *,
    hearing_ready: bool = True,
    voice_ready: bool = True,
    voice: str = A_SPEECH_VOICE,
) -> dict[str, object]:
    """A `/health` body; `voice` names what the service offers."""
    return {
        "speaking": {
            "ready": True,
            "voices": {voice: {"model": "m", "ready": voice_ready, "sample_rate": 1}},
        },
        "hearing": {"model": "m", "ready": hearing_ready},
        "card_memory_mb": 1,
    }


def a_refusal(code: wire.RefusalCode, param: wire.RequestField | None = None) -> Answer:
    """A speak refusal in the wire's own form, as the service builds it."""
    refusal = wire.refusal(code, param)
    return lambda: JSONResponse(
        refusal.model_dump(mode="json", exclude={"status"}),
        status_code=refusal.status,
    )


def a_chunked_wav(audio: bytes = A_WAV) -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield audio

    return StreamingResponse(chunks(), media_type="audio/wav")


async def accept_and_hold(socket_to_browser: WebSocket) -> None:
    await socket_to_browser.accept()
    await socket_to_browser.receive()


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
    arrived: threading.Semaphore = field(default_factory=threading.Semaphore)
    connected: threading.Event = field(default_factory=threading.Event)

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
def a_served_app(app: FastAPI) -> Generator[str]:
    """Serve `app` from a thread of its own, for a caller with a loop of its own."""
    ready = threading.Event()
    served: list[str] = []
    failed: list[BaseException] = []
    stops: list[Callable[[], object]] = []

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        stops.append(lambda: loop.call_soon_threadsafe(stop.set))
        async with a_loopback_server(app) as origin:
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


@dataclass(frozen=True, slots=True)
class RawPeer:
    """Answers whatever bytes a scenario gives on every connection, then closes."""

    answer: bytes

    async def _serve(
        self, _reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
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


def a_closed_port_origin() -> str:
    """An origin nothing listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        host, port = probe.getsockname()
    return f"http://{host}:{port}"
