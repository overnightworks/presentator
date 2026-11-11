"""How the hearing adapter turns the service's events into the transcripts shown."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from fastapi import WebSocket
from speech_client import wire
from speech_client.relay import HOP_FRAME_BYTES, HopClose

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import HearingTranscript, SpeechTokens
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    AN_ITEM,
    FakeSpeechService,
    Hear,
    a_completed,
    a_delta,
    a_failed_item,
    a_loopback_server,
    accept_and_hold,
    an_error_event,
    sending,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

TOKENS = SpeechTokens(person="person-token-MARKER", workload="workload-token-MARKER")
_CLOSE_WAIT_SECONDS = 10


@dataclass
class ASilentBrowser:
    """A browser socket that sends its queued frames, then nothing.

    It keeps the code it was closed with.
    """

    closed_with: list[int] = field(default_factory=list[int])
    sends: list[dict[str, object]] = field(default_factory=list[dict[str, object]])

    async def receive(self) -> Mapping[str, object]:
        if self.sends:
            return self.sends.pop(0)
        await asyncio.Future()
        raise AssertionError

    async def close(self, code: int) -> None:
        self.closed_with.append(code)


async def _heard_through(
    realtime: Hear, browser: ASilentBrowser | None = None
) -> tuple[list[HearingTranscript], ASilentBrowser]:
    fake = FakeSpeechService(realtime=realtime)
    browser = browser or ASilentBrowser()
    transcripts: list[HearingTranscript] = []

    async def heard(transcript: HearingTranscript) -> None:
        transcripts.append(transcript)

    async with a_loopback_server(fake.app()) as origin:
        await SpeechHearing(origin, voice=A_SPEECH_VOICE).relay(
            browser, "de", TOKENS, ended=asyncio.Event(), heard=heard
        )
    return transcripts, browser


def _transcripts(*shown: tuple[str, bool]) -> list[HearingTranscript]:
    return [HearingTranscript(text=text, final=final) for text, final in shown]


def test_deltas_grow_per_item_and_the_completed_text_replaces_them() -> None:
    events = (
        a_delta("Gu", item="one"),
        a_delta("Ha", item="two"),
        a_delta("ten", item="one"),
        a_delta("llo", item="two"),
        a_completed("Guten Tag.", item="one"),
        a_completed("Hallo.", item="two"),
    )

    transcripts, browser = asyncio.run(_heard_through(sending(*events)))

    assert transcripts == _transcripts(
        ("Gu", False),
        ("Ha", False),
        ("Guten", False),
        ("Hallo", False),
        ("Guten Tag.", True),
        ("Hallo.", True),
    )
    assert browser.closed_with == [HopClose.NORMAL]


def test_an_empty_completed_sends_nothing_and_clears_the_item_for_its_next_delta() -> (
    None
):
    events = (a_delta("Ha"), a_completed(""), a_delta("Neu"), a_completed("Neu."))

    transcripts, _browser = asyncio.run(_heard_through(sending(*events)))

    assert transcripts == _transcripts(("Ha", False), ("Neu", False), ("Neu.", True))


def test_session_and_turn_events_are_skipped() -> None:
    session = wire.TranscriptionSession(type="transcription")
    events = (
        wire.SessionUpdated(session=session),
        wire.SpeechStarted(audio_start_ms=0, item_id=AN_ITEM),
        wire.SpeechStopped(audio_end_ms=1, item_id=AN_ITEM),
        wire.AudioCommitted(item_id=AN_ITEM, previous_item_id=None),
        wire.ItemCreated(previous_item_id=None, item=wire.UserMessageItem(id=AN_ITEM)),
        a_completed("Ein Wort."),
    )

    transcripts, _browser = asyncio.run(_heard_through(sending(*events)))

    assert transcripts == _transcripts(("Ein Wort.", True))


@pytest.mark.parametrize("event", [a_failed_item(), an_error_event()], ids=type)
def test_a_failed_item_or_error_event_ends_the_hearing_as_a_service_failure(
    event: wire.ServerEvent,
) -> None:
    transcripts, browser = asyncio.run(
        _heard_through(sending(a_delta("Ha"), event, a_delta("never shown")))
    )

    assert transcripts == _transcripts(("Ha", False))
    assert browser.closed_with == [HopClose.SERVICE_FAILED]


@pytest.mark.parametrize(
    ("frame", "close"),
    [
        ({"type": "websocket.receive", "text": "not pcm"}, HopClose.TEXT_FRAME),
        (
            {"type": "websocket.receive", "bytes": b"\0" * (HOP_FRAME_BYTES + 1)},
            HopClose.FRAME_TOO_LARGE,
        ),
    ],
    ids=["text-frame", "oversize-frame"],
)
def test_a_frame_that_breaks_the_hop_rules_closes_the_browser_with_one_warning(
    frame: dict[str, object],
    close: HopClose,
    caplog: pytest.LogCaptureFixture,
) -> None:
    browser = ASilentBrowser(sends=[frame])

    with caplog.at_level(logging.WARNING, logger="presentator"):
        asyncio.run(_heard_through(accept_and_hold, browser))

    assert browser.closed_with == [close]
    assert [record.getMessage() for record in caplog.records] == [
        f"hearing failed: RuleBroken close={close.value}"
    ]


class _CallerFaultError(Exception):
    pass


def test_a_fault_of_the_caller_passes_through_and_closes_the_service_side() -> None:
    fake = FakeSpeechService()

    async def realtime(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text(a_delta("Ha").model_dump_json())
        await fake.read_events(socket)

    fake.realtime = realtime

    async def scenario() -> list[str]:
        async def heard(transcript: HearingTranscript) -> None:
            del transcript
            raise _CallerFaultError

        async with a_loopback_server(fake.app()) as origin:
            hearing = SpeechHearing(origin, voice=A_SPEECH_VOICE)
            relaying = hearing.relay(
                ASilentBrowser(), "de", TOKENS, ended=asyncio.Event(), heard=heard
            )
            with pytest.raises(_CallerFaultError):
                await relaying
            assert await asyncio.to_thread(fake.closed.wait, _CLOSE_WAIT_SECONDS)
            return fake.closes

    assert asyncio.run(scenario()) == ["closed"]
