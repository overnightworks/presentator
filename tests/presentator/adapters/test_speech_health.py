"""Readiness from the service's open `/health`, against the in-process fake."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.responses import JSONResponse, Response
from speech_runtime_client import wire

from presentator.adapters.speech import SpeechHearing
from presentator.contracts.copresenter import (
    CoPresenterReadiness,
    CoPresenterUnavailableError,
)
from tests.support.speech_service import (
    A_SPEECH_ORIGIN,
    A_SPEECH_VOICE,
    FakeSpeech,
    a_health,
)


def readiness_of(fake: FakeSpeech) -> CoPresenterReadiness:
    hearing = SpeechHearing(A_SPEECH_ORIGIN, A_SPEECH_VOICE, transports=fake.transports)
    return asyncio.run(hearing.readiness())


def test_a_service_that_hears_and_speaks_is_ready_at_the_realtime_rate() -> None:
    """R1: the rate is the client's Realtime input rate."""
    ready = readiness_of(FakeSpeech())

    assert ready == CoPresenterReadiness(
        hearing_sample_rate=wire.SessionInput().format.rate, local_hearing_ready=True
    )


@pytest.mark.parametrize("voice_is_ready", [True, False])
def test_hearing_that_is_still_loading_is_not_ready_whatever_the_voice_does(
    *, voice_is_ready: bool
) -> None:
    """R2: 200 with hearing not ready, also while the voice loads."""
    health = a_health(hearing_ready=False, voice_ready=voice_is_ready)

    ready = readiness_of(FakeSpeech(health_answer=JSONResponse(health)))

    assert ready.local_hearing_ready is False


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(JSONResponse({"detail": "x"}, status_code=503), id="non-200"),
        pytest.param(Response("not json"), id="non-json"),
        pytest.param(JSONResponse({"hearing": a_health()["hearing"]}), id="no-voices"),
        pytest.param(
            JSONResponse({**a_health(), "hearing": {"model": "m", "ready": "maybe"}}),
            id="ready-fails-coercion",
        ),
    ],
)
def test_an_answer_that_is_no_health_is_unavailable(answer: Response) -> None:
    """R3, G4: no exception of the unreadable body is kept."""
    with pytest.raises(CoPresenterUnavailableError) as failed:
        readiness_of(FakeSpeech(health_answer=answer))

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)


def test_an_unreachable_origin_is_unavailable() -> None:
    """R3."""
    hearing = SpeechHearing("http://127.0.0.1:1", A_SPEECH_VOICE)

    with pytest.raises(CoPresenterUnavailableError) as failed:
        asyncio.run(hearing.readiness())

    assert (failed.value.__cause__, failed.value.__context__) == (None, None)


@pytest.mark.parametrize(
    "health",
    [
        pytest.param(a_health(voice="another"), id="absent"),
        pytest.param(a_health(voice_ready=False), id="not-ready"),
    ],
)
def test_a_voice_that_is_not_there_or_not_ready_is_unavailable(
    health: dict[str, object],
) -> None:
    """R4: the asked voice decides, not another one."""
    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(FakeSpeech(health_answer=JSONResponse(health)))


def test_a_service_of_the_older_shape_is_unavailable() -> None:
    """R4: a service older than the client fails loud."""
    older = {
        "speaking": {"model": "voice", "ready": True, "sample_rate": 22_050},
        "hearing": {"model": "whisper", "ready": True},
        "sample_rate": 16_000,
        "card_memory_mb": 1_200,
    }

    with pytest.raises(CoPresenterUnavailableError):
        readiness_of(FakeSpeech(health_answer=JSONResponse(older)))


def test_the_health_request_carries_no_credential() -> None:
    """R5."""
    fake = FakeSpeech()

    readiness_of(fake)

    (received,) = fake.healths
    assert (
        not {
            "cookie",
            "authorization",
            wire.WORKLOAD_TOKEN_HEADER.lower(),
        }
        & received.headers.keys()
    )
