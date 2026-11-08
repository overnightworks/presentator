"""Readiness read from the shared speech service's `/health`."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.responses import JSONResponse
from speech_runtime_client import wire
from speech_runtime_client.outcomes import PastDeadline

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
)
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    HEALTH_FAILURES,
    MALFORMED_PEERS,
    FakeSpeechService,
    RawPeer,
    a_closed_port_origin,
    a_health_document,
    a_loopback_server,
)


def readiness_of(fake: FakeSpeechService) -> CoPresenterReadiness:
    async def scenario() -> CoPresenterReadiness:
        async with a_loopback_server(fake.app()) as origin:
            return await SpeechHearing(origin, voice=A_SPEECH_VOICE).readiness()

    return asyncio.run(scenario())


def test_a_ready_service_hears_at_the_clients_fixed_rate() -> None:
    readiness = readiness_of(FakeSpeechService())

    assert readiness == CoPresenterReadiness(
        hearing_sample_rate=wire.SessionInput().format.rate, local_hearing_ready=True
    )


@pytest.mark.parametrize("voice_ready", [True, False], ids=["voice-ready", "voice-not"])
def test_hearing_still_loading_is_not_ready_whatever_the_voice_says(
    *, voice_ready: bool
) -> None:
    body = a_health_document(hearing_ready=False, voice_ready=voice_ready)
    fake = FakeSpeechService(health_answer=lambda: JSONResponse(body))

    assert readiness_of(fake).local_hearing_ready is False


def test_the_health_request_carries_no_credential() -> None:
    fake = FakeSpeechService()

    readiness_of(fake)

    (request,) = fake.health_requests
    assert not {"cookie", "authorization", wire.WORKLOAD_TOKEN_HEADER.lower()} & (
        request.headers.keys()
    )


@pytest.mark.parametrize("case", HEALTH_FAILURES)
def test_every_answer_but_a_ready_health_is_unavailable(case: str) -> None:
    fake = FakeSpeechService(health_answer=HEALTH_FAILURES[case])

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)


def test_a_health_read_past_its_deadline_is_unavailable() -> None:
    def bounded_health_fetch(*_args: object, **_kwargs: object) -> PastDeadline:
        return PastDeadline()

    hearing = SpeechHearing(
        "http://speech.test",
        voice=A_SPEECH_VOICE,
        bounded_health_fetch=bounded_health_fetch,
    )

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(hearing.readiness())


def test_a_refused_connection_is_unavailable() -> None:
    hearing = SpeechHearing(a_closed_port_origin(), voice=A_SPEECH_VOICE)

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(hearing.readiness())


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
            await SpeechHearing(origin, voice=A_SPEECH_VOICE).readiness()

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())
