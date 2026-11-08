"""Hearing over the shared speech service's Realtime connection."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest
from fastapi import Response, WebSocket
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
)
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.copresenter_support import PrivateFakes, a_use
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    AN_ITEM,
    FakeSpeechService,
    Hear,
    RawPeer,
    a_closed_port_origin,
    a_completed,
    a_delta,
    a_failed_item,
    a_loopback_server,
    an_error_event,
    sending,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from presentator.ports.copresenter import PrivateHearing

TOKENS = SpeechTokens(person="person-token-MARKER", workload="workload-token-MARKER")
ITEM = AN_ITEM
_CLOSE_WAIT_SECONDS = 10


@asynccontextmanager
async def hearing_against(
    fake: FakeSpeechService, language: str = "de"
) -> AsyncGenerator[PrivateHearing]:
    async with (
        a_loopback_server(fake.app()) as origin,
        SpeechHearing(origin, voice=A_SPEECH_VOICE).hear(language, TOKENS) as hearing,
    ):
        yield hearing


def _heard_all(fake: FakeSpeechService) -> list[HearingTranscript | str | None]:
    async def scenario() -> list[HearingTranscript | str | None]:
        heard: list[HearingTranscript | str | None] = []
        async with hearing_against(fake) as hearing:
            while True:
                event = await hearing.receive()
                heard.append(event)
                if event is None:
                    return heard

    return asyncio.run(scenario())


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_upgrade_carries_the_tokens_and_the_intent_then_names_the_language(
    language: str,
) -> None:
    fake = FakeSpeechService()

    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await fake.read_events(socket)

    fake.realtime = realtime

    async def scenario() -> None:
        async with hearing_against(fake, language) as hearing:
            await hearing.send_pcm(b"pcm")

    asyncio.run(scenario())

    (upgrade,) = fake.upgrades
    sent = wire.token_headers(
        workload_token=TOKENS.workload, person_token=TOKENS.person
    )
    for name, value in sent.items():
        assert upgrade.headers[name.lower()] == value
    assert fake.upgrade_paths == [
        f"{wire.REALTIME_INTENT_PARAM}={wire.REALTIME_INTENT}"
    ]
    setup, append = fake.events
    assert setup["type"] == "session.update"
    session_input = setup["session"]["audio"]["input"]  # type: ignore[index]
    assert session_input["transcription"]["language"] == language  # type: ignore[index]
    assert append["type"] == "input_audio_buffer.append"


def test_another_language_is_unavailable_with_no_upgrade() -> None:
    fake = FakeSpeechService()

    async def scenario() -> None:
        async with hearing_against(fake, "fr"):
            pass

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())

    assert not fake.upgrades


def test_each_frame_is_appended_in_order_and_closing_sends_no_commit() -> None:
    fake = FakeSpeechService()

    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await fake.read_events(socket)

    fake.realtime = realtime
    frames = (b"first", b"second", b"third")

    async def scenario() -> None:
        async with hearing_against(fake) as hearing:
            for frame in frames:
                await hearing.send_pcm(frame)

    asyncio.run(scenario())

    appended = [event for event in fake.events if event["type"] != "session.update"]
    assert appended == [
        {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(frame).decode("ascii"),
        }
        for frame in frames
    ]
    assert fake.closes == ["closed"]


def test_deltas_grow_per_item_and_the_completed_text_replaces_them() -> None:
    fake = FakeSpeechService(
        realtime=sending(
            a_delta("Gu"),
            a_delta("Ha", item="item-2"),
            a_delta("ten"),
            a_completed("Guten Tag."),
        )
    )

    heard = _heard_all(fake)

    assert heard == [
        HearingTranscript(text="Gu", final=False),
        HearingTranscript(text="Ha", final=False),
        HearingTranscript(text="Guten", final=False),
        HearingTranscript(text="Guten Tag.", final=True),
        None,
    ]


def test_an_empty_completed_sends_nothing_and_clears_the_item_for_its_next_delta() -> (
    None
):
    fake = FakeSpeechService(
        realtime=sending(
            a_delta("Ha", item="item-2"),
            a_completed("", item="item-2"),
            a_delta("Neu", item="item-2"),
        )
    )

    assert _heard_all(fake) == [
        HearingTranscript(text="Ha", final=False),
        HearingTranscript(text="Neu", final=False),
        None,
    ]


def test_session_and_turn_events_are_skipped() -> None:
    fake = FakeSpeechService(
        realtime=sending(
            wire.SessionCreated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.SessionUpdated(
                session=wire.TranscriptionSession(type="transcription")
            ),
            wire.ItemCreated(previous_item_id=None, item=wire.UserMessageItem(id=ITEM)),
            wire.SpeechStarted(audio_start_ms=0, item_id=ITEM),
            wire.SpeechStopped(audio_end_ms=1, item_id=ITEM),
            wire.AudioCommitted(item_id=ITEM, previous_item_id=None),
            a_completed("Ein Wort."),
        )
    )

    assert _heard_all(fake) == [
        HearingTranscript(text="Ein Wort.", final=True),
        None,
    ]


@pytest.mark.parametrize(
    "events",
    [
        pytest.param(
            lambda: (a_failed_item(), an_error_event()), id="failed-then-error"
        ),
        pytest.param(lambda: (an_error_event(),), id="error-alone"),
    ],
)
def test_a_failed_item_or_error_event_is_unavailable(
    events: Callable[[], tuple[wire.ServerEvent, ...]],
) -> None:
    fake = FakeSpeechService(realtime=sending(*events()))

    with pytest.raises(CoPresenterUnavailableError):
        _heard_all(fake)


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(
            json.dumps({"type": "unknown.event", "event_id": "e1"}), id="unknown-type"
        ),
        pytest.param(
            json.dumps(
                {
                    "type": "conversation.item.input_audio_transcription.delta",
                    "event_id": "e2",
                    "item_id": ITEM,
                    "delta": ["not", "text"],
                }
            ),
            id="invalid-payload",
        ),
        pytest.param("not json at all", id="non-json"),
    ],
)
def test_a_frame_the_parser_refuses_is_unavailable(frame: str) -> None:
    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text(frame)

    fake = FakeSpeechService(realtime=realtime)

    async def scenario() -> None:
        async with hearing_against(fake) as hearing:
            await hearing.receive()

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


@pytest.mark.parametrize("code", [1000, 1009, 1011, 1013])
def test_every_close_without_a_terminal_event_ends_the_hearing_quietly(
    code: int,
) -> None:
    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await socket.close(code=code)

    assert _heard_all(FakeSpeechService(realtime=realtime)) == [None]


def test_a_peer_that_crashes_mid_session_ends_the_hearing() -> None:
    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        message = "the service crashed"
        raise RuntimeError(message)

    async def scenario() -> HearingTranscript | str | None:
        async with hearing_against(FakeSpeechService(realtime=realtime)) as hearing:
            try:
                await hearing.send_pcm(b"pcm")
            except CoPresenterUnavailableError:
                return "unavailable"
            try:
                return await hearing.receive()
            except CoPresenterUnavailableError:
                return "unavailable"

    assert asyncio.run(scenario()) in (None, "unavailable")


def test_audio_sent_after_the_service_closed_is_unavailable() -> None:
    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await socket.close()

    async def scenario() -> None:
        async with hearing_against(FakeSpeechService(realtime=realtime)) as hearing:
            assert await hearing.receive() is None
            await hearing.send_pcm(b"pcm")

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


async def _refuse_with(socket: WebSocket, answer: Response) -> None:
    await socket.send_denial_response(answer)


def _denying(status: int, headers: dict[str, str] | None = None) -> Hear:
    async def realtime(socket: WebSocket) -> None:
        await _refuse_with(socket, Response(status_code=status, headers=headers))

    return realtime


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        pytest.param(429, {"Retry-After": "3"}, id="busy"),
        pytest.param(401, None, id="unauthorized"),
        pytest.param(403, None, id="forbidden"),
        pytest.param(503, None, id="unavailable"),
    ],
)
def test_an_upgrade_refused_by_the_service_is_unavailable(
    status: int, headers: dict[str, str] | None
) -> None:
    fake = FakeSpeechService(realtime=_denying(status, headers))

    async def scenario() -> None:
        async with hearing_against(fake):
            pass

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


def test_a_malformed_upgrade_response_is_unavailable() -> None:
    async def scenario() -> None:
        async with (
            RawPeer(b"HTTP/1.1 204 No Content\r\n\r\n").serving() as origin,
            SpeechHearing(origin, voice=A_SPEECH_VOICE).hear("de", TOKENS),
        ):
            pass

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


def test_a_refused_connection_is_unavailable() -> None:
    hearing = SpeechHearing(a_closed_port_origin(), voice=A_SPEECH_VOICE)

    async def scenario() -> None:
        async with hearing.hear("de", TOKENS):
            pass

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


class _CallerFaultError(Exception):
    pass


def test_a_fault_of_the_caller_is_not_mapped() -> None:
    fake = FakeSpeechService()

    async def scenario() -> None:
        async with hearing_against(fake):
            raise _CallerFaultError

    with pytest.raises(ExceptionGroup) as propagated:
        asyncio.run(scenario())

    assert propagated.value.subgroup(_CallerFaultError) is not None


def test_ending_the_hearing_closes_the_service_side() -> None:
    async def scenario() -> list[str]:
        fake = FakeSpeechService()

        async def realtime(socket: WebSocket) -> None:
            await socket.accept()
            await fake.read_events(socket)

        fake.realtime = realtime
        use = a_use(PrivateFakes(), FakeDeckStore())
        async with a_loopback_server(fake.app()) as origin:
            use = replace(use, speech=SpeechHearing(origin, voice=A_SPEECH_VOICE))
            async with use.hear("de", session_id="a-session"):
                pass
            assert await asyncio.to_thread(fake.closed.wait, _CLOSE_WAIT_SECONDS)
            return fake.closes

    assert asyncio.run(scenario()) == ["closed"]
