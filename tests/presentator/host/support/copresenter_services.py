"""What a co-presenter app reaches: a gateway, a speech service, a browser socket."""

import asyncio
import json
import socket
import threading
from collections.abc import AsyncGenerator, Generator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from typing import Any, Final

from agents.backend.fake_gateway import completion
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from speech_client import realtime_wire
from starlette.responses import Response
from wsproto import ConnectionType, WSConnection
from wsproto.events import (
    AcceptConnection,
    BytesMessage,
    CloseConnection,
    Event,
    TextMessage,
)
from wsproto.events import (
    Request as UpgradeRequest,
)

from tests.presentator.host.support.app_process import REFRESH_TOKEN_VARIABLE
from tests.support.speech_service import (
    FakeSpeechService,
    a_completed,
    a_loopback_server,
    a_served_origin,
)

_SECONDS: Final = 30
_ANSWER: Final = "Ein Satz."
_END_OF_THE_FIRST_EVENT: Final = b"\n\n"


@dataclass(slots=True)
class HeldGateway:
    """A gateway whose answer stops after its first event until the test lets it go."""

    started: threading.Event = field(default_factory=threading.Event)
    release: threading.Event = field(default_factory=threading.Event)

    def app(self) -> FastAPI:
        """The gateway as an ASGI app, answering every POST."""
        app = FastAPI()
        app.add_api_route("/{path:path}", self._answer, methods=["POST"])
        return app

    async def _answer(self, request: Request) -> Response:
        await request.body()
        body = completion(_ANSWER).body
        first = body.index(_END_OF_THE_FIRST_EVENT) + len(_END_OF_THE_FIRST_EVENT)

        async def events() -> AsyncGenerator[bytes]:
            yield body[:first]
            self.started.set()
            await asyncio.to_thread(self.release.wait, _SECONDS)
            yield body[first:]

        return StreamingResponse(events(), media_type="text/event-stream")


async def _hears_what_is_said(socket_to_browser: WebSocket) -> None:
    """Take the first audio of each hearing, answer it with a finished utterance."""
    await socket_to_browser.accept()
    await socket_to_browser.send_text(
        realtime_wire.SessionCreated(
            session=realtime_wire.TranscriptionSession(type="transcription")
        ).model_dump_json()
    )
    await socket_to_browser.receive()
    await socket_to_browser.receive()
    await socket_to_browser.send_text(a_completed("Hallo").model_dump_json())
    while (await socket_to_browser.receive())["type"] != "websocket.disconnect":
        pass


@contextmanager
def a_gateway_and_a_speech_service(gateway: HeldGateway) -> Generator[dict[str, str]]:
    """The environment that points an app at both, served for the block."""
    with (
        a_served_origin(lambda: a_loopback_server(gateway.app())) as gateway_origin,
        a_served_origin(
            lambda: a_loopback_server(
                FakeSpeechService(realtime=_hears_what_is_said).app()
            )
        ) as speech_origin,
    ):
        yield {
            "PRESENTATOR_AGENT__GATEWAY__ORIGIN": gateway_origin,
            "PRESENTATOR_SPEECH_SERVICE": speech_origin,
            REFRESH_TOKEN_VARIABLE: "a-refresh-token",
        }


HEARING_TARGET: Final = "/copresenter/hear?language=de"


class HearingSocket:
    """A browser's `/hear` or talk WebSocket to one app process, read to its close."""

    def __init__(  # noqa: PLR0913 - the browser's address and what it sends
        self,
        port: int,
        *,
        host: str,
        origin: str,
        cookie: str,
        target: str = HEARING_TARGET,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        """Open the socket at the app's port, as the browser at `origin` does."""
        self._socket = socket.create_connection(("127.0.0.1", port), timeout=_SECONDS)
        self._connection = WSConnection(ConnectionType.CLIENT)
        self._send(
            UpgradeRequest(
                host=host,
                target=target,
                extra_headers=[
                    (b"origin", origin.encode()),
                    (b"cookie", cookie.encode()),
                    *(
                        (name.encode(), value.encode())
                        for name, value in (headers or {}).items()
                    ),
                ],
            )
        )
        assert isinstance(self._next_event(), AcceptConnection)

    def _send(self, event: Event) -> None:
        self._socket.sendall(self._connection.send(event))

    def _next_event(self) -> Event:
        while True:
            for event in self._connection.events():
                return event
            data = self._socket.recv(65536)
            self._connection.receive_data(data or None)

    def hear_after_saying(self) -> str:
        """Send one frame of audio; the utterance it is heard as."""
        self._send(BytesMessage(data=b"\0\0" * 4))
        event = self._next_event()
        assert isinstance(event, TextMessage), event
        return event.data

    def next_frame(self, kind: str) -> dict[str, Any]:
        """The next JSON frame of this kind; the frames before it are dropped."""
        while True:
            event = self._next_event()
            assert isinstance(event, TextMessage), event
            frame: dict[str, Any] = json.loads(event.data)
            if frame["kind"] == kind:
                return frame

    def send_json(self, frame: Mapping[str, object]) -> None:
        """Send one JSON message up, as the browser does."""
        self._send(TextMessage(data=json.dumps(frame)))

    def stays_open_for(self, seconds: float) -> bool:
        """Whether nothing arrives, the server's close included, for this long."""
        self._socket.settimeout(seconds)
        try:
            self._next_event()
        except TimeoutError:
            return True
        finally:
            self._socket.settimeout(_SECONDS)
        return False

    def close_code(self) -> int:
        """The code the server closes the socket with, once it does."""
        event = self._next_event()
        assert isinstance(event, CloseConnection), event
        return event.code

    def close(self) -> None:
        """Let go of the connection."""
        self._socket.close()


@contextmanager
def a_hearing(
    port: int, *, host: str, origin: str, cookie: str, target: str = HEARING_TARGET
) -> Generator[HearingSocket]:
    """One hearing, or the talk the target names, open for the block."""
    hearing = HearingSocket(
        port, host=host, origin=origin, cookie=cookie, target=target
    )
    with closing(hearing):
        yield hearing
