"""Readiness read from the shared speech service's `/health`."""

from __future__ import annotations

import asyncio
from typing import Final

import pytest
from fastapi.responses import JSONResponse
from speech_client import wire
from speech_client.outcomes import PastDeadline

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
)
from presentator.contracts.words import LANGUAGES, Language
from tests.support.lobby import the_speech_voices
from tests.support.log_lines import LoggedText, json_logged
from tests.support.speech_service import (
    HEALTH_FAILURES,
    MALFORMED_PEERS,
    PLANTED,
    FakeSpeechService,
    RawPeer,
    a_closed_port_origin,
    a_health_document,
    a_loopback_server,
)


def readiness_of(fake: FakeSpeechService) -> CoPresenterReadiness:
    async def scenario() -> CoPresenterReadiness:
        async with a_loopback_server(fake.app()) as origin:
            return await SpeechHearing(origin, voices=the_speech_voices()).readiness()

    return asyncio.run(scenario())


def test_a_ready_service_is_ready_to_hear() -> None:
    readiness = readiness_of(FakeSpeechService())

    assert readiness == CoPresenterReadiness(local_hearing_ready=True)


@pytest.mark.parametrize("voice_ready", [True, False], ids=["voice-ready", "voice-not"])
def test_hearing_still_loading_is_not_ready_whatever_the_voice_says(
    *, voice_ready: bool
) -> None:
    body = a_health_document(hearing_ready=False, voice_ready=voice_ready)
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    assert readiness_of(fake).local_hearing_ready is False


def test_a_listed_voice_not_ready_yet_on_a_ready_service_is_available() -> None:
    body = a_health_document(voice_ready=False, speaking_ready=True)
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    assert readiness_of(fake).local_hearing_ready is True


def test_a_listed_voice_on_a_service_in_its_start_load_is_unavailable() -> None:
    body = a_health_document(voice_ready=False, speaking_ready=False)
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_a_service_not_offering_the_voice_of_one_language_is_unavailable(
    language: Language,
) -> None:
    voices = dict(the_speech_voices())
    del voices[language]
    body = a_health_document(
        voices_offered={str(voice): True for voice in voices.values()}
    )
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_one_languages_voice_not_ready_in_the_start_load_is_unavailable(
    language: Language,
) -> None:
    voices_offered = {
        str(voice): voice != the_speech_voices()[language]
        for voice in the_speech_voices().values()
    }
    body = a_health_document(voices_offered=voices_offered, speaking_ready=False)
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)


def test_the_health_request_carries_no_credential() -> None:
    fake = FakeSpeechService()

    readiness_of(fake)

    (request,) = fake.health_requests
    assert not {"cookie", "authorization", wire.WORKLOAD_TOKEN_HEADER.lower()} & (
        request.headers.keys()
    )


def the_readiness_failure(logged: LoggedText) -> tuple[object, object]:
    """The `(reason, status)` of the one readiness failure line; no planted text."""
    (line,) = [one for one in logged.lines if one["event"] == "speech_readiness.failed"]
    assert PLANTED not in logged.text
    return line["reason"], line.get("status")


# The `reason` of the `speech_readiness.failed` line of every failure.
_READINESS_FAILURE_REASONS: Final[dict[str, str]] = {
    "non-200-marked": "unexpected_status",
    "garbage-marked": "malformed",
    "missing-section": "malformed",
    "ready-as-text": "malformed",
    "ready-as-number": "malformed",
    "sample-rate-as-text": "malformed",
    "past-the-cap": "over_cap",
    "cut-body": "cut",
    "v0.1-shape": "malformed",
    "voice-absent": "voice_unready",
    "service-in-its-start-load": "voice_unready",
}


@pytest.mark.parametrize("case", HEALTH_FAILURES)
def test_every_answer_but_a_ready_health_is_unavailable(
    case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    answer = HEALTH_FAILURES[case]
    reason = _READINESS_FAILURE_REASONS[case]
    status = answer().status_code if reason == "unexpected_status" else None
    fake = FakeSpeechService(health_answer=answer)

    with json_logged(capsys) as logged, pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)

    assert the_readiness_failure(logged) == (reason, status)


def test_a_health_read_past_its_deadline_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def bounded_health_fetch(*_args: object, **_kwargs: object) -> PastDeadline:
        return PastDeadline()

    hearing = SpeechHearing(
        "http://speech.test",
        voices=the_speech_voices(),
        bounded_health_fetch=bounded_health_fetch,
    )

    running = hearing.readiness()
    with json_logged(capsys) as logged, pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)

    assert the_readiness_failure(logged) == ("past_deadline", None)


def test_a_refused_connection_is_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    hearing = SpeechHearing(a_closed_port_origin(), voices=the_speech_voices())

    running = hearing.readiness()
    with json_logged(capsys) as logged, pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)

    assert the_readiness_failure(logged) == ("unreachable", None)


@pytest.mark.parametrize(
    "answer",
    [
        *MALFORMED_PEERS.values(),
        b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort",
    ],
    ids=[*MALFORMED_PEERS, "body-cut-midway"],
)
def test_a_malformed_or_cut_answer_is_unavailable(answer: bytes) -> None:
    async def scenario() -> None:
        async with RawPeer(answer).serving() as origin:
            await SpeechHearing(origin, voices=the_speech_voices()).readiness()

    running = scenario()
    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)
