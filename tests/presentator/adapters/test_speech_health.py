"""Readiness read from the shared speech service's `/health`."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi.responses import JSONResponse, Response, StreamingResponse
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
)
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    FakeSpeechService,
    RawPeer,
    a_closed_port_origin,
    a_health_document,
    a_loopback_server,
)

_HEALTH_BODY_CAP_BYTES = 70_000


def readiness_of(fake: FakeSpeechService) -> CoPresenterReadiness:
    async def scenario() -> CoPresenterReadiness:
        async with a_loopback_server(fake.app()) as origin:
            return await SpeechHearing(origin, voice=A_SPEECH_VOICE).readiness()

    return asyncio.run(scenario())


def _health(**changes: object) -> Callable[[], Response]:
    return lambda: JSONResponse({**a_health_document(), **changes})


def _a_cut_body() -> Response:
    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"speaking":'
        message = "cut"
        raise RuntimeError(message)

    return StreamingResponse(chunks(), media_type="application/json")


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


def _v0_1_shape() -> Response:
    return JSONResponse(
        {
            "speaking": {"model": "m", "ready": True, "sample_rate": 22050},
            "hearing": {"model": "m", "ready": True},
            "sample_rate": 16000,
            "card_memory_mb": 1,
        }
    )


def _absent_voice() -> Response:
    return JSONResponse(a_health_document(voice="another-voice"))


def _its_voice_not_ready() -> Response:
    return JSONResponse(a_health_document(voice_ready=False))


NOT_READY: dict[str, Callable[[], Response]] = {
    "non-200": lambda: JSONResponse({}, status_code=503),
    "non-json": lambda: Response(b"not json", media_type="application/json"),
    "missing-section": _health(hearing=None),
    "ready-as-text": _health(hearing={"model": "m", "ready": "true"}),
    "ready-as-number": _health(hearing={"model": "m", "ready": 1}),
    "sample-rate-as-text": _health(
        speaking={
            "ready": True,
            "voices": {
                A_SPEECH_VOICE: {"model": "m", "ready": True, "sample_rate": "1"}
            },
        }
    ),
    "past-the-cap": lambda: Response(b"x" * _HEALTH_BODY_CAP_BYTES),
    "cut-body": _a_cut_body,
    "v0.1-shape": _v0_1_shape,
    "voice-absent": _absent_voice,
    "its-voice-not-ready": _its_voice_not_ready,
}


@pytest.mark.parametrize("case", NOT_READY)
def test_every_answer_but_a_ready_health_is_unavailable(case: str) -> None:
    fake = FakeSpeechService(health_answer=NOT_READY[case])

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(fake)


def test_a_refused_connection_is_unavailable() -> None:
    hearing = SpeechHearing(a_closed_port_origin(), voice=A_SPEECH_VOICE)

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(hearing.readiness())


@pytest.mark.parametrize(
    "answer",
    [b"NOT HTTP\r\n\r\n", b"HTTP/1.1 200 OK\r\nContent-Length: 9\r\n\r\nshort"],
    ids=["status-line-not-http", "body-cut-midway"],
)
def test_a_malformed_or_cut_answer_is_unavailable(answer: bytes) -> None:
    async def scenario() -> None:
        async with RawPeer(answer).serving() as origin:
            await SpeechHearing(origin, voice=A_SPEECH_VOICE).readiness()

    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(scenario())
