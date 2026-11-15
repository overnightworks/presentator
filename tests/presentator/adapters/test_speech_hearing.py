"""How the hearing adapter turns the service's events into the transcripts shown."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from speech_client import wire
from speech_client.relay import HOP_FRAME_BYTES, HopClose

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
)
from presentator.contracts.voice import SampleLanguage
from tests.support.lobby import the_speech_voices
from tests.support.speech_service import (
    AN_ITEM,
    FakeSpeechService,
    Hear,
    RecordedRequest,
    a_completed,
    a_delta,
    a_failed_item,
    a_loopback_server,
    accept_and_hold,
    an_error_event,
    busy_at_the_upgrade,
    draining,
    one_after_another,
    sending,
    sending_then_reading,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class MintedTokens:
    """A token source that mints a new pair per call, or fails on one call."""

    failing_call: int | None = None
    calls: int = 0

    async def __call__(self) -> SpeechTokens:
        self.calls += 1
        if self.calls == self.failing_call:
            raise CoPresenterUnavailableError
        return self.pair(self.calls)

    @staticmethod
    def pair(call: int) -> SpeechTokens:
        return SpeechTokens(
            person=f"person-token-{call}", workload=f"workload-token-{call}"
        )


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
    realtime: Hear,
    browser: ASilentBrowser | None = None,
    tokens: MintedTokens | None = None,
    fake: FakeSpeechService | None = None,
) -> tuple[list[HearingTranscript], ASilentBrowser]:
    fake = fake or FakeSpeechService()
    fake.realtime = realtime
    browser = browser or ASilentBrowser()
    transcripts: list[HearingTranscript] = []

    async def heard(transcript: HearingTranscript) -> None:
        transcripts.append(transcript)

    async with a_loopback_server(fake.app()) as origin:
        await SpeechHearing(origin, voices=the_speech_voices()).relay(
            browser, "de", tokens or MintedTokens(), ended=asyncio.Event(), heard=heard
        )
    return transcripts, browser


def _interim(text: str) -> HearingTranscript:
    return HearingTranscript(text=text, final=False)


def _final(
    text: str, language: SampleLanguage = SampleLanguage.GERMAN
) -> HearingTranscript:
    return HearingTranscript(text=text, final=True, language=language)


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

    assert transcripts == [
        _interim("Gu"),
        _interim("Ha"),
        _interim("Guten"),
        _interim("Hallo"),
        _final("Guten Tag."),
        _final("Hallo."),
    ]
    assert browser.closed_with == [HopClose.NORMAL]


@pytest.mark.parametrize("language", list(SampleLanguage))
def test_a_completed_transcript_carries_the_language_the_hearing_decoded_it_in(
    language: SampleLanguage,
) -> None:
    events = (
        a_delta("Hello"),
        a_completed("Hello there.", language=wire.Language(language.value)),
    )

    transcripts, _browser = asyncio.run(_heard_through(sending(*events)))

    assert transcripts == [_interim("Hello"), _final("Hello there.", language)]


def test_an_empty_completed_sends_nothing_and_clears_the_item_for_its_next_delta() -> (
    None
):
    events = (a_delta("Ha"), a_completed(""), a_delta("Neu"), a_completed("Neu."))

    transcripts, _browser = asyncio.run(_heard_through(sending(*events)))

    assert transcripts == [_interim("Ha"), _interim("Neu"), _final("Neu.")]


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

    assert transcripts == [_final("Ein Wort.")]


@pytest.mark.parametrize("event", [a_failed_item(), an_error_event()], ids=type)
def test_a_failed_item_or_error_event_ends_the_hearing_as_a_service_failure(
    event: wire.ServerEvent,
) -> None:
    transcripts, browser = asyncio.run(
        _heard_through(sending(a_delta("Ha"), event, a_delta("never shown")))
    )

    assert transcripts == [_interim("Ha")]
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
    fake.realtime = sending_then_reading(fake, a_delta("Ha"))

    async def scenario() -> list[str]:
        async def heard(transcript: HearingTranscript) -> None:
            del transcript
            raise _CallerFaultError

        async with a_loopback_server(fake.app()) as origin:
            hearing = SpeechHearing(origin, voices=the_speech_voices())
            relaying = hearing.relay(
                ASilentBrowser(),
                "de",
                MintedTokens(),
                ended=asyncio.Event(),
                heard=heard,
            )
            with pytest.raises(_CallerFaultError):
                await relaying
            assert await asyncio.to_thread(fake.closed.wait, _CLOSE_WAIT_SECONDS)
            return fake.closes

    assert asyncio.run(scenario()) == ["closed"]


def _presented(upgrade: RecordedRequest) -> tuple[str, str]:
    headers = upgrade.headers
    return headers[wire.WORKLOAD_TOKEN_HEADER.lower()], headers[
        wire.AUTHORIZATION_HEADER.lower()
    ].removeprefix("Bearer ")


def test_each_opening_presents_the_tokens_its_source_minted_for_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSpeechService()
    tokens = MintedTokens()
    realtime = one_after_another(
        draining(), draining(), sending(a_completed("Wieder da."))
    )

    with caplog.at_level(logging.WARNING, logger="presentator"):
        transcripts, browser = asyncio.run(
            _heard_through(realtime, tokens=tokens, fake=fake)
        )

    assert transcripts == [_final("Wieder da.")]
    assert browser.closed_with == [HopClose.NORMAL]
    assert caplog.records == []
    assert [_presented(upgrade) for upgrade in fake.upgrades] == [
        (minted.workload, minted.person) for minted in map(MintedTokens.pair, (1, 2, 3))
    ]


@pytest.mark.parametrize("failing_call", [1, 2], ids=["first-opening", "reopening"])
def test_tokens_that_cannot_be_read_end_the_hearing_as_a_service_failure(
    failing_call: int,
) -> None:
    fake = FakeSpeechService()

    transcripts, browser = asyncio.run(
        _heard_through(
            one_after_another(draining(), sending(a_completed("Nie gehört."))),
            tokens=MintedTokens(failing_call=failing_call),
            fake=fake,
        )
    )

    assert transcripts == []
    assert browser.closed_with == [HopClose.SERVICE_FAILED]
    assert len(fake.upgrades) == failing_call - 1


def test_a_hearing_the_service_never_reopens_ends_as_a_service_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="presentator"):
        transcripts, browser = asyncio.run(
            _heard_through(one_after_another(draining(), busy_at_the_upgrade()))
        )

    assert transcripts == []
    assert browser.closed_with == [HopClose.SERVICE_FAILED]
    assert [record.getMessage() for record in caplog.records] == [
        "hearing failed: ReconnectFailed"
    ]
