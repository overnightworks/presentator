"""Speaking one sentence through the shared service, against the in-process fake."""

from __future__ import annotations

import asyncio
import logging

import httpx2
import pytest
from fastapi.responses import Response
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechSpeaking
from presentator.contracts.copresenter import CoPresenterUnavailableError, SpeechTokens
from tests.support.speech_service import (
    A_MARKED_WAV,
    A_SPEECH_ORIGIN,
    A_SPEECH_VOICE,
    BreakingTransport,
    FakeSpeech,
    a_refusal,
)

TOKENS = SpeechTokens(person="person-MARKER", workload="workload-MARKER")
MARKER = "echoed-MARKER"


def speaking_through(fake: FakeSpeech) -> SpeechSpeaking:
    return SpeechSpeaking(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=fake.transports)


def speak(fake: FakeSpeech, text: str = "Eins.") -> bytes:
    return asyncio.run(speaking_through(fake).speak(text, "de", TOKENS))


def test_a_speak_request_carries_the_persons_token_and_the_apps_own() -> None:
    """T1."""
    fake = FakeSpeech()

    speak(fake)

    (received,) = fake.speaks
    assert received.headers["authorization"] == f"Bearer {TOKENS.person}"
    assert received.headers[wire.WORKLOAD_TOKEN_HEADER.lower()] == TOKENS.workload


def test_the_body_names_the_configured_voice_and_the_sentence_and_no_language() -> None:
    """T2: `SpeechRequest` forbids any other field, so parsing it proves the body."""
    fake = FakeSpeech()

    speak(fake, "Ein Satz.")

    request = wire.SpeechRequest.model_validate_json(fake.speaks[0].body)
    assert (request.voice, request.input) == (A_SPEECH_VOICE, "Ein Satz.")


def test_the_wav_passes_through_unchanged_with_its_ai_mark() -> None:
    """T4."""
    assert speak(FakeSpeech()) == A_MARKED_WAV


def test_a_sentence_without_a_letter_or_digit_is_unavailable_and_sent_nowhere() -> None:
    """T5: the client refuses to build the body."""
    fake = FakeSpeech()

    with pytest.raises(CoPresenterUnavailableError) as failed:
        speak(fake, "…")

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)

    assert fake.speaks == []


@pytest.mark.parametrize("code", list(wire.RefusalCode))
def test_every_refusal_of_the_wire_is_unavailable_and_logs_only_closed_values(
    code: wire.RefusalCode, caplog: pytest.LogCaptureFixture
) -> None:
    """T5."""
    fake = FakeSpeech(speak_refusal=a_refusal(code, "voice", message=MARKER))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        speak(fake)

    assert MARKER not in caplog.text
    assert code.value in caplog.text


@pytest.mark.parametrize("retry_after", [None, "3", "soon"])
def test_a_busy_answer_ends_the_speak_whatever_it_says_about_the_wait(
    retry_after: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """T5."""
    refusal = a_refusal(wire.RefusalCode.BUSY, retry_after=retry_after)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        speak(FakeSpeech(speak_refusal=refusal))

    if retry_after is not None:
        assert f"wait={retry_after if retry_after.isdigit() else 'malformed'}" in (
            caplog.text
        )


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(Response(MARKER, status_code=500), id="not-json"),
        pytest.param(Response(b"", media_type="audio/wav"), id="empty-body"),
    ],
)
def test_an_answer_that_is_no_wav_and_no_refusal_is_unavailable(
    answer: Response, caplog: pytest.LogCaptureFixture
) -> None:
    """T5."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        speak(FakeSpeech(speak_refusal=answer))

    assert MARKER not in caplog.text


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx2.RemoteProtocolError("cut off"), id="truncated-body"),
        pytest.param(httpx2.ReadTimeout("stalled"), id="stalled-body"),
    ],
)
def test_a_body_that_breaks_is_unavailable_and_the_connection_is_closed(
    error: httpx2.TransportError,
) -> None:
    """T5, T6: a missing end of the chunked body is a failed request."""
    broken = BreakingTransport(error)
    speaking = SpeechSpeaking(
        A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=lambda: broken
    )

    with pytest.raises(CoPresenterUnavailableError) as failed:
        asyncio.run(speaking.speak("Eins.", "de", TOKENS))

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)
    assert broken.body.closed.is_set()


def test_a_refused_connection_is_unavailable() -> None:
    """T5."""

    def refusing() -> httpx2.AsyncBaseTransport:
        return httpx2.MockTransport(_refuse)

    speaking = SpeechSpeaking(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=refusing)

    with pytest.raises(CoPresenterUnavailableError) as failed:
        asyncio.run(speaking.speak("Eins.", "de", TOKENS))

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)


def _refuse(request: httpx2.Request) -> httpx2.Response:
    message = "refused"
    raise httpx2.ConnectError(message, request=request)


def test_a_cancelled_speak_closes_the_connection() -> None:
    """T6: a browser that leaves mid-answer cancels the speak."""
    stalled = BreakingTransport(None)
    speaking = SpeechSpeaking(
        A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=lambda: stalled
    )

    async def scenario() -> None:
        task = asyncio.create_task(speaking.speak("Eins.", "de", TOKENS))
        await stalled.body.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert stalled.body.closed.is_set()
