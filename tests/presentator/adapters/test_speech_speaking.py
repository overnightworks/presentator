"""Speaking one sentence at the shared speech service, through the client."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import pytest
from speech_runtime_client import wire
from speech_runtime_client.outcomes import OverCap, PastDeadline

from presentator.adapters.speech import SpeechSpeaking
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    SpeechTokens,
)
from tests.support.lobby import A_SPEECH_VOICE
from tests.support.speech_service import (
    A_WAV,
    MALFORMED_PEERS,
    PLANTED,
    SPEAK_FAILURES,
    FakeSpeechService,
    RawPeer,
    a_closed_port_origin,
    a_loopback_server,
)

if TYPE_CHECKING:
    from speech_runtime_client.speak import SpeakOutcome

TOKENS = SpeechTokens(person="person-token-MARKER", workload="workload-token-MARKER")
SENTENCE = "Ein Satz, bitte."


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


@pytest.mark.parametrize("case", SPEAK_FAILURES)
def test_every_refusal_or_broken_answer_is_unavailable(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeSpeechService(speak_answers=[SPEAK_FAILURES[case]])
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        speak_against(fake)

    assert TOKENS.person not in caplog.text
    assert TOKENS.workload not in caplog.text
    assert SENTENCE not in caplog.text
    assert PLANTED not in caplog.text


def test_a_refused_connection_is_unavailable() -> None:
    speaking = SpeechSpeaking(a_closed_port_origin(), voice=A_SPEECH_VOICE)

    running = speaking.speak(SENTENCE, "de", TOKENS)
    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)


@pytest.mark.parametrize("case", MALFORMED_PEERS)
def test_a_malformed_answer_is_unavailable(case: str) -> None:
    async def scenario() -> None:
        async with RawPeer(MALFORMED_PEERS[case]).serving() as origin:
            await SpeechSpeaking(origin, voice=A_SPEECH_VOICE).speak(
                SENTENCE, "de", TOKENS
            )

    running = scenario()
    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)


@pytest.mark.parametrize("outcome", [PastDeadline(), OverCap()], ids=repr)
def test_a_speak_the_client_ended_at_its_own_bound_is_unavailable(
    outcome: SpeakOutcome,
) -> None:
    def bounded_speak(*_args: object, **_kwargs: object) -> SpeakOutcome:
        return outcome

    speaking = SpeechSpeaking(
        "http://speech.test", voice=A_SPEECH_VOICE, bounded_speak=bounded_speak
    )

    running = speaking.speak(SENTENCE, "de", TOKENS)
    with pytest.raises(CoPresenterUnavailableError):
        asyncio.run(running)


def test_a_sentence_the_wire_cannot_carry_is_one_chain_free_unavailable_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = FakeSpeechService()
    caplog.set_level(logging.DEBUG)

    async def scenario() -> None:
        async with a_loopback_server(fake.app()) as origin:
            await SpeechSpeaking(origin, voice=A_SPEECH_VOICE).speak("…", "de", TOKENS)

    running = scenario()
    with pytest.raises(CoPresenterUnavailableError) as refused:
        asyncio.run(running)

    assert (refused.value.__cause__, refused.value.__context__) == (None, None)
    assert not fake.speak_requests
    assert "…" not in caplog.text


def test_a_startup_defect_is_not_mapped() -> None:
    speaking = SpeechSpeaking("https://not-plain-http.test", voice=A_SPEECH_VOICE)

    running = speaking.speak(SENTENCE, "de", TOKENS)
    with pytest.raises(ValueError, match="http"):
        asyncio.run(running)
