"""Drives the talk socket through the fake speech service and the fake gateway."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, NoReturn, cast

import pytest
from agents.backend.fake_gateway import FakeGateway, Reply, completion
from speech_client import wire
from starlette.websockets import WebSocketDisconnect

from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from tests.presentator.application.support.ask_leases import FakeAskTicket
from tests.support.copresenter_support import (
    THE_ASKED_DECK,
    RecordingLimiter,
    nothing_happens,
)
from tests.support.lobby import A_TALK_SOCKET_ADDRESS
from tests.support.speech_drive import (
    ORIGIN,
    THREAD_WAIT_SECONDS,
    SpeechScenario,
    a_speech_instance,
)
from tests.support.speech_service import Cues, FakeSpeechService, a_completed
from tests.support.talk_support import A_DECK

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from sqlalchemy import Engine
    from starlette.testclient import WebSocketTestSession

    from tests.support.speech_drive import SpeechInstance
    from tests.support.talk_support import Hosts

TALK: Final = f"{A_TALK_SOCKET_ADDRESS}/copresenter/talk"
A_LINE: Final = "Hallo zusammen."
SENTENCES: Final = ("Erster Satz.", "Zweiter Satz.")
TWO_SENTENCES: Final = " ".join(SENTENCES)


class UnavailableLimiter(RecordingLimiter):
    """The ask lease whose store does not answer."""

    async def admit(self, session_id: str) -> RetryAfter:
        """Raise: the store is down."""
        raise CoPresenterUnavailableError


class _FreedSessions(list[str]):
    """The sessions whose lease was freed; adding one tells the test thread."""

    def __init__(self, told: threading.Event) -> None:
        super().__init__()
        self._told = told

    def append(self, session_id: str, /) -> None:
        super().append(session_id)
        self._told.set()


class SignalledLimiter(RecordingLimiter):
    """The ask lease that tells the test thread when a ticket was freed."""

    def __init__(self) -> None:
        """Start with nothing freed."""
        super().__init__()
        self.freed_once = threading.Event()
        self.freed = _FreedSessions(self.freed_once)


class HeldByAnotherPod(RecordingLimiter):
    """The ask lease whose first session is held by a ticket of another pod."""

    async def admit(self, session_id: str) -> FakeAskTicket | RetryAfter:
        """Take the session's ticket for the other pod, and refuse the caller."""
        admitted = await super().admit(session_id)
        if isinstance(admitted, RetryAfter):
            return admitted
        return RetryAfter(BUSY_RETRY_SECONDS)


@dataclass(kw_only=True)
class Talking:
    """One open talk socket and the service's cues."""

    socket: WebSocketTestSession
    cues: Cues
    instance: SpeechInstance

    def say(
        self,
        text: str = A_LINE,
        *,
        language: wire.Language = wire.Language.DE,
        traceparent: str | None = None,
    ) -> None:
        """The person finishes a line; the service tells the hearing."""
        self.cues.send(a_completed(text, language=language, traceparent=traceparent))

    def frame(self) -> dict[str, object]:
        """The next frame down."""
        return self.socket.receive_json()

    def until(self, kind: str) -> list[dict[str, object]]:
        """The frames down up to and including the first of this kind."""
        frames = [self.frame()]
        while frames[-1]["kind"] != kind:
            frames.append(self.frame())
        return frames

    def next_sound(self) -> dict[str, object]:
        """The next sound sent down."""
        return self.until("audio")[-1]

    def played(self, sound: object, end: str = "ended") -> None:
        """The browser says how a sound ended."""
        self.up({"kind": "played", "id": sound, "end": end})

    def play_through(self) -> dict[str, object]:
        """Take the next sound and play it to its end."""
        sound = self.next_sound()
        self.played(sound["id"])
        return sound

    def shown(self, slide: int) -> None:
        """The browser shows this slide."""
        self.up({"kind": "shown", "slide": slide})

    @property
    def session_id(self) -> str:
        """The session this talk is signed in by, as the lease saw it."""
        return self.instance.limiter.session_ids[0]

    def until_closed(self) -> int:
        """Take the frames down until the socket closes; the code it closed with."""
        with pytest.raises(WebSocketDisconnect) as closed:
            self._frames_forever()
        return closed.value.code

    def _frames_forever(self) -> NoReturn:
        while True:
            self.frame()

    def until_freed(self) -> None:
        """Return once the talk freed the session's lease.

        The test client cancels a route that is still running when its socket is
        left, so what a talk does after a decision or a leave is seen by waiting here.
        """
        limiter = self.instance.limiter
        assert isinstance(limiter, SignalledLimiter)
        assert limiter.freed_once.wait(THREAD_WAIT_SECONDS)

    def leave(self) -> None:
        """The browser leaves in order; the call returns once the lease is free."""
        self.socket.close()
        self.until_freed()

    def ask(self) -> int:
        """The status of a question this person's browser asks over HTTP."""
        return self.instance.talk.post(
            "/copresenter/ask", json=THE_ASKED_DECK
        ).status_code

    def up(self, frame: object) -> None:
        """Send a message up, as written."""
        self.up_text(json.dumps(frame))

    def up_text(self, text: str) -> None:
        """Send this text up as the browser's message."""
        self.socket.send_text(text)

    def microphone(self, sound: bytes = b"sound") -> None:
        """Send a binary frame up; the messages before it are read once this returns."""
        self.socket.send_bytes(sound)
        assert self.instance.service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)


@contextmanager
def a_talk(
    engine: Engine,
    *replies: Reply,
    deck: str = A_DECK,
    limiter: Callable[[], RecordingLimiter] = SignalledLimiter,
    arrange: Callable[[Hosts], None] = nothing_happens,
) -> Generator[Talking]:
    """A signed-in person's open talk about a deck, the service connected.

    The gateway gives these replies, the last one repeating.
    """
    cues = Cues()
    service = FakeSpeechService()
    service.realtime = cues.hear(service)
    scenario = SpeechScenario(
        service=service,
        gateway=lambda: FakeGateway(*(replies or (completion(TWO_SENTENCES),))),
        limiter=limiter,
        arrange=arrange,
    )
    with (
        a_speech_instance(engine, scenario) as instance,
        instance.talk.websocket_connect(
            f"{TALK}?deck={deck}&language=de", headers=ORIGIN
        ) as socket,
    ):
        socket.send_bytes(b"first")
        assert cues.connected.wait(THREAD_WAIT_SECONDS)
        for _ in range(2):
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)
        yield Talking(socket=socket, cues=cues, instance=instance)


def last_lines_of(talking: Talking) -> list[str]:
    """The last line each request the gateway got carries, in order."""
    return [lines_in(request)[-1]["text"] for request in requests_of(talking)]


def requests_of(talking: Talking) -> list[dict[str, Any]]:
    """The bodies of the requests the gateway got, in order."""
    gateway = cast("FakeGateway", talking.instance.gateway)
    return [json.loads(request.content) for request in gateway.requests]


def system_text_of(body: dict[str, Any]) -> str:
    """What the model is told before the talk: its prompt, the protocol and the deck."""
    return body["messages"][0]["content"]


def lines_in(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The lines of the request's last user message, as the model reads them."""
    return [json.loads(line) for line in body["messages"][-1]["content"].splitlines()]


def closed_at_open(engine: Engine, url: str) -> tuple[int, int]:
    """The close code of a talk refused at open, and the gateway's request count."""
    scenario = SpeechScenario(gateway=lambda: FakeGateway(completion(TWO_SENTENCES)))
    with (
        a_speech_instance(engine, scenario) as instance,
        pytest.raises(WebSocketDisconnect) as closed,
        instance.talk.websocket_connect(url, headers=ORIGIN) as socket,
    ):
        socket.receive_json()
    return closed.value.code, len(cast("FakeGateway", instance.gateway).requests)
