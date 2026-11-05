"""Hearing through the service's Realtime transcription, against the in-process fake."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING

import httpx2
import pytest
from fastapi.responses import JSONResponse
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    HearingTranscript,
    SpeechTokens,
)
from tests.support.speech_service import (
    A_SPEECH_ORIGIN,
    A_SPEECH_VOICE,
    DELTA,
    FakeSpeech,
    a_refusal,
    completed,
    delta,
    error_event,
    failure,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

TOKENS = SpeechTokens(person="person-MARKER", workload="workload-MARKER")
MARKER = "said-MARKER"


def hearing_through(fake: FakeSpeech) -> SpeechHearing:
    return SpeechHearing(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=fake.transports)


def run[Result](
    fake: FakeSpeech, use: Callable[[SpeechHearing], Awaitable[Result]]
) -> Result:
    return asyncio.run(use(hearing_through(fake)))


async def hear_everything(
    hearing_service: SpeechHearing, language: str = "de", frames: int = 1
) -> list[HearingTranscript | None]:
    async with hearing_service.hear(language, TOKENS) as hearing:
        for _ in range(frames):
            await hearing.send_pcm(b"\x01\x02")
        return [await hearing.receive() for _ in range(2)]


def heard(fake: FakeSpeech, language: str = "de") -> list[HearingTranscript | None]:
    return run(fake, lambda service: hear_everything(service, language))


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_upgrade_carries_both_tokens_in_headers_and_one_session_update_first(
    language: str,
) -> None:
    """H1."""
    fake = FakeSpeech(after_audio=[completed("x"), completed("y")])

    heard(fake, language)

    (upgrade,) = fake.upgrades
    assert upgrade.query == {wire.REALTIME_INTENT_PARAM: wire.REALTIME_INTENT}
    assert upgrade.headers["authorization"] == f"Bearer {TOKENS.person}"
    assert upgrade.headers[wire.WORKLOAD_TOKEN_HEADER.lower()] == TOKENS.workload
    assert "content-type" not in upgrade.headers
    assert fake.events == ["session.update", "input_audio_buffer.append"]
    update = wire.SessionUpdate.model_validate_json(json.dumps(fake.sent[0]))
    assert update.session.audio.input.transcription.language.value == language


def test_a_language_the_service_does_not_hear_is_no_hearing() -> None:
    """H1."""
    fake = FakeSpeech()

    with pytest.raises(CoPresenterUnavailableError):
        heard(fake, "fr")

    assert fake.upgrades == []


def test_each_browser_frame_becomes_one_append_with_the_same_bytes_in_order() -> None:
    """H2."""
    fake = FakeSpeech(after_audio=[completed("x")])

    async def use(service: SpeechHearing) -> None:
        async with service.hear("de", TOKENS) as hearing:
            await hearing.send_pcm(b"\x01\x02")
            await hearing.receive()

    run(fake, use)

    append = wire.AudioAppend.model_validate_json(json.dumps(fake.sent[1]))
    assert base64.b64decode(append.audio) == b"\x01\x02"


def test_closing_sends_no_commit_and_the_service_sees_the_close() -> None:
    """H2."""
    fake = FakeSpeech()

    async def use(service: SpeechHearing) -> None:
        fake.client_closed = asyncio.Event()
        async with service.hear("de", TOKENS) as hearing:
            await hearing.send_pcm(b"\x01\x02")
        await asyncio.wait_for(fake.client_closed.wait(), timeout=5)

    run(fake, use)

    assert "input_audio_buffer.commit" not in fake.events


def test_deltas_grow_per_item_and_completed_replaces_them() -> None:
    """H3: two interleaved items stay apart; the final is the completed text."""
    fake = FakeSpeech(
        after_audio=[
            delta("Hallo", "a"),
            delta("Eins", "b"),
            delta(" Welt", "a"),
            completed("Hallo Welt!", "a"),
        ]
    )

    async def use(service: SpeechHearing) -> list[HearingTranscript | None]:
        async with service.hear("de", TOKENS) as hearing:
            await hearing.send_pcm(b"\x01\x02")
            return [await hearing.receive() for _ in range(4)]

    assert run(fake, use) == [
        HearingTranscript(text="Hallo", final=False),
        HearingTranscript(text="Eins", final=False),
        HearingTranscript(text="Hallo Welt", final=False),
        HearingTranscript(text="Hallo Welt!", final=True),
    ]


def test_an_empty_completed_sends_nothing_and_clears_the_item() -> None:
    """H3."""
    fake = FakeSpeech(
        after_audio=[
            delta("Hm", "a"),
            completed("", "a"),
            delta("Neu", "a"),
            completed("Neu.", "a"),
        ],
        closes_with=1000,
    )

    async def use(service: SpeechHearing) -> list[HearingTranscript | None]:
        async with service.hear("de", TOKENS) as hearing:
            await hearing.send_pcm(b"\x01\x02")
            return [await hearing.receive() for _ in range(4)]

    assert run(fake, use) == [
        HearingTranscript(text="Hm", final=False),
        HearingTranscript(text="Neu", final=False),
        HearingTranscript(text="Neu.", final=True),
        None,
    ]


def test_events_the_overlay_does_not_need_are_skipped() -> None:
    """H3."""
    skipped: list[dict[str, object]] = [
        {"type": "session.created", "session": {}},
        {"type": "input_audio_buffer.speech_started", "item_id": "a"},
        {"type": "input_audio_buffer.speech_stopped"},
        {"type": "input_audio_buffer.committed"},
        {"type": "conversation.item.created"},
        {"type": "some.future.event"},
    ]
    fake = FakeSpeech(after_audio=[*skipped, completed("Fertig.")], closes_with=1000)

    assert heard(fake)[0] == HearingTranscript(text="Fertig.", final=True)


@pytest.mark.parametrize(
    "ending",
    [[failure(), error_event()], [error_event()], [failure()]],
    ids=["failed-then-error", "error-alone", "failed-alone"],
)
def test_a_failure_the_service_reports_is_no_hearing_and_shows_nothing_it_said(
    ending: list[object], caplog: pytest.LogCaptureFixture
) -> None:
    """H4."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        heard(FakeSpeech(after_audio=ending))

    assert MARKER not in caplog.text


@pytest.mark.parametrize(
    "refusal",
    [
        a_refusal(wire.RefusalCode.BUSY, retry_after="3", message=MARKER),
        a_refusal(wire.RefusalCode.UNAUTHORIZED),
        a_refusal(wire.RefusalCode.NOT_ENABLED),
        a_refusal(wire.RefusalCode.UNAVAILABLE),
    ],
    ids=["busy", "unauthorized", "not-enabled", "unavailable"],
)
def test_a_refused_upgrade_is_no_hearing(
    refusal: JSONResponse, caplog: pytest.LogCaptureFixture
) -> None:
    """H5."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        heard(FakeSpeech(upgrade_refusal=refusal))

    assert MARKER not in caplog.text


def test_an_upgrade_a_plain_transport_refuses_is_no_hearing_and_logs_the_wait(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H5: the refusal reaches the adapter as the upgrade error itself."""
    refusal = a_refusal(wire.RefusalCode.BUSY, retry_after="3", message=MARKER)

    def answer(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(
            refusal.status_code, headers=refusal.headers, content=bytes(refusal.body)
        )

    service = SpeechHearing(
        A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=lambda: httpx2.MockTransport(answer)
    )
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(hear_everything(service))

    assert "wait=3" in caplog.text
    assert MARKER not in caplog.text


def test_a_refused_connection_is_no_hearing() -> None:
    """H5."""
    unreachable = SpeechHearing("http://127.0.0.1:1", A_SPEECH_VOICE)

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(hear_everything(unreachable))


@pytest.mark.parametrize("code", [1000, 1009, 1011, 1013])
def test_a_close_without_a_terminal_event_ends_the_hearing_quietly(code: int) -> None:
    """H6."""
    fake = FakeSpeech(after_audio=[delta("Hm")], closes_with=code)

    assert heard(fake) == [HearingTranscript(text="Hm", final=False), None]


def test_a_peer_that_crashes_mid_session_ends_the_hearing_quietly() -> None:
    """H6."""
    fake = FakeSpeech(after_audio=[delta("Hm")], crashes=True)

    assert heard(fake) == [HearingTranscript(text="Hm", final=False), None]


@pytest.mark.parametrize(
    "frame",
    [
        {"type": DELTA, "item_id": "a", "content_index": 0, "delta": ["not", "text"]},
        "not json at all-MARKER",
        b"binary-MARKER",
        '["a list"]',
    ],
    ids=["invalid-payload", "non-json", "binary", "not-an-object"],
)
def test_a_frame_that_is_not_a_modelled_event_is_no_hearing(
    frame: object, caplog: pytest.LogCaptureFixture
) -> None:
    """H7."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        heard(FakeSpeech(after_audio=[frame]))

    assert MARKER not in caplog.text


class _CallerFaultError(Exception):
    pass


def test_a_fault_of_the_caller_is_not_mapped() -> None:
    """H8."""
    fake = FakeSpeech()

    async def use(service: SpeechHearing) -> None:
        async with service.hear("de", TOKENS):
            raise _CallerFaultError

    with pytest.raises(ExceptionGroup) as propagated:
        run(fake, use)

    assert propagated.value.subgroup(_CallerFaultError) is not None
