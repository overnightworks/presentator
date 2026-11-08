"""Speaking one sentence at the shared speech service, through the client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import pytest
from fastapi.responses import JSONResponse, Response, StreamingResponse
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechSpeaking
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    SpeechTokens,
)
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    A_WAV,
    FakeSpeechService,
    RawPeer,
    a_closed_port_origin,
    a_loopback_server,
    a_refusal,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tests.support.speech_service import Answer

TOKENS = SpeechTokens(person="person-token-MARKER", workload="workload-token-MARKER")
SENTENCE = "Ein Satz, bitte."
MARKER = "planted-MARKER"


def speak_against(fake: FakeSpeechService) -> bytes:
    async def scenario() -> bytes:
        async with a_loopback_server(fake.app()) as origin:
            return await SpeechSpeaking(origin, voice=A_SPEECH_VOICE).speak(
                SENTENCE, "de", TOKENS
            )

    return asyncio.run(scenario())


def test_the_audio_passes_through_unchanged() -> None:
    assert speak_against(FakeSpeechService()) == A_WAV


def test_the_request_names_the_configured_voice_format_and_exactly_the_sentence() -> (
    None
):
    fake = FakeSpeechService()

    speak_against(fake)

    (request,) = fake.speak_requests
    body = json.loads(request.body)
    assert body["voice"] == A_SPEECH_VOICE
    assert body["response_format"] == wire.ResponseFormat.WAV.value
    assert body["input"] == SENTENCE


def test_the_request_carries_both_tokens_in_the_clients_headers() -> None:
    fake = FakeSpeechService()

    speak_against(fake)

    (request,) = fake.speak_requests
    sent = wire.token_headers(
        workload_token=TOKENS.workload, person_token=TOKENS.person
    )
    for name, value in sent.items():
        assert request.headers[name.lower()] == value


def _a_cut_body() -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield A_WAV
        message = "cut"
        raise RuntimeError(message)

    return StreamingResponse(chunks(), media_type="audio/wav")


def _a_voice_that_cannot_speak() -> Answer:
    return a_refusal(wire.RefusalCode.UNAVAILABLE, "voice")


def _busy_with_a_wait() -> Response:
    refusal = wire.refusal(wire.RefusalCode.BUSY)
    return JSONResponse(
        refusal.model_dump(mode="json", exclude={"status"}),
        status_code=refusal.status,
        headers={wire.RETRY_AFTER_HEADER: "3"},
    )


REFUSALS: dict[str, Answer] = {
    **{
        code.value: a_refusal(code)
        for code in wire.RefusalCode
        if code is not wire.RefusalCode.BUSY
    },
    "unavailable-voice": _a_voice_that_cannot_speak(),
    "busy": _busy_with_a_wait,
    "non-json-500": lambda: Response(b"not json", status_code=500),
    "empty-body": lambda: StreamingResponse(iter(()), media_type="audio/wav"),
    "cut-body": _a_cut_body,
}


@pytest.mark.parametrize("case", REFUSALS)
def test_every_refusal_or_broken_answer_is_unavailable(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeSpeechService(speak_answers=[REFUSALS[case]])
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        speak_against(fake)

    assert TOKENS.person not in caplog.text
    assert TOKENS.workload not in caplog.text
    assert SENTENCE not in caplog.text


def test_a_refused_connection_is_unavailable() -> None:
    speaking = SpeechSpeaking(a_closed_port_origin(), voice=A_SPEECH_VOICE)

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(speaking.speak(SENTENCE, "de", TOKENS))


@pytest.mark.parametrize(
    "answer",
    [b"HTTP/9 marker not a status line\r\n\r\n", b"HTTP/1.1 200 OK\r\nX-Cut: "],
    ids=["status-line-not-http", "cut-header-block"],
)
def test_a_malformed_answer_is_unavailable(answer: bytes) -> None:
    async def scenario() -> None:
        async with RawPeer(answer).serving() as origin:
            await SpeechSpeaking(origin, voice=A_SPEECH_VOICE).speak(
                SENTENCE, "de", TOKENS
            )

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())


def test_a_sentence_the_wire_cannot_carry_is_one_chain_free_unavailable_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSpeechService()
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with a_loopback_server(fake.app()) as origin:
            await SpeechSpeaking(origin, voice=A_SPEECH_VOICE).speak("…", "de", TOKENS)

    with pytest.raises(CoPresenterUnavailableError) as refused:
        asyncio.run(scenario())

    assert (refused.value.__cause__, refused.value.__context__) == (None, None)
    assert not fake.speak_requests
    assert "…" not in caplog.text


def test_a_startup_defect_is_not_mapped() -> None:
    speaking = SpeechSpeaking("https://not-plain-http.test", voice=A_SPEECH_VOICE)

    with pytest.raises(ValueError, match="http"):
        asyncio.run(speaking.speak(SENTENCE, "de", TOKENS))
