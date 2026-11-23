"""Drives the talk socket through the fake speech service and the fake gateway."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, cast

import pytest
from agents.backend.fake_gateway import FakeGateway, Reply, completion
from speech_client import wire
from starlette.websockets import WebSocketDisconnect

from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from tests.support.copresenter_support import (
    RecordingLimiter,
    nothing_happens,
)
from tests.support.lobby import A_TALK_SOCKET_ADDRESS
from tests.support.speech_drive import ORIGIN, SpeechScenario, a_speech_instance
from tests.support.speech_service import Cues, FakeSpeechService, a_completed
from tests.support.talk_support import A_DECK

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from fastapi.testclient import WebSocketTestSession
    from sqlalchemy import Engine

    from tests.support.speech_drive import SpeechInstance
    from tests.support.talk_support import Hosts

TALK: Final = f"{A_TALK_SOCKET_ADDRESS}/copresenter/talk"
A_LINE: Final = "Hallo zusammen."
TWO_SENTENCES: Final = "Erster Satz. Zweiter Satz."
CONNECT_SECONDS: Final = 10


class UnavailableLimiter(RecordingLimiter):
    """The ask lease whose store does not answer."""

    async def admit(self, session_id: str) -> RetryAfter:
        """Raise: the store is down."""
        raise CoPresenterUnavailableError


class HeldElsewhere(RecordingLimiter):
    """The ask lease another process holds, for every session."""

    async def admit(self, session_id: str) -> RetryAfter:
        """Refuse, naming the wait."""
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

    def up(self, frame: object) -> None:
        """Send a message up, as written."""
        self.socket.send_text(json.dumps(frame))


@contextmanager
def a_talk(
    engine: Engine,
    *replies: Reply,
    deck: str = A_DECK,
    limiter: Callable[[], RecordingLimiter] = RecordingLimiter,
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
        assert cues.connected.wait(CONNECT_SECONDS)
        yield Talking(socket=socket, cues=cues, instance=instance)


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
    """The close code of a talk the route refuses at open, and the gateway's requests."""
    scenario = SpeechScenario(gateway=lambda: FakeGateway(completion(TWO_SENTENCES)))
    with (
        a_speech_instance(engine, scenario) as instance,
        pytest.raises(WebSocketDisconnect) as closed,
        instance.talk.websocket_connect(url, headers=ORIGIN) as socket,
    ):
        socket.receive_json()
    return closed.value.code, len(cast("FakeGateway", instance.gateway).requests)
