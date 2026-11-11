"""The talk host's co-presenter routes over the real speech adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlsplit

import pytest
from agents.backend.fake_gateway import FakeGateway, completion
from fastapi.responses import JSONResponse
from speech_client import wire

from tests.support.copresenter_support import (
    ACCESS,
    ROTATED,
    delete_the_sessions,
    the_refresh_token_is_revoked,
)
from tests.support.leak_support import everything_shown
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_SPEECH_SCOPE,
    A_TALK_ADDRESS,
)
from tests.support.speech_drive import (
    LEAK_ASKS,
    LEAK_HEALTH,
    WORKLOAD,
    SpeechInstance,
    SpeechScenario,
    a_speech_instance,
    ask_and_leave_at_the_first_speak,
    ask_over,
    assert_nothing_shows,
    refusing_every_workload_grant,
    who_over,
)
from tests.support.speech_service import (
    A_WAV,
    HEALTH_FAILURES,
    MALFORMED_PEERS,
    PLANTED,
    SPEAK_FAILURES,
    FakeSpeechService,
    RawPeer,
    Served,
    a_chunked_wav,
    a_health_document,
    a_held_wav,
    nobody_listens,
)
from tests.support.talk_support import Hosts

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_POLICY_VIOLATION: Final = 1008
THREE_SENTENCES: Final = 3
SECOND: Final = 2
_THE_PLAIN_ERROR: Final = {"message": "the answer could not be spoken"}


def _say(text: str) -> Callable[[], FakeGateway]:
    return lambda: FakeGateway(completion(text))


def _spoken_wavs(body: str) -> list[bytes]:
    return [
        base64.b64decode(json.loads(data)["wav_b64"])
        for data in re.findall(r"^event: audio\ndata: (.*)$", body, re.MULTILINE)
    ]


def test_sentences_are_spoken_in_order_with_both_issued_tokens_and_the_wav_unchanged(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz. Dritter."), service=service
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.OK
    assert [
        json.loads(request.body)["input"] for request in service.speak_requests
    ] == [
        "Erster Satz.",
        "Zweiter Satz.",
        "Dritter.",
    ]
    for request in service.speak_requests:
        assert request.headers["authorization"] == f"Bearer {ACCESS}"
        assert request.headers[wire.WORKLOAD_TOKEN_HEADER.lower()] == WORKLOAD
    assert _spoken_wavs(run.response.text) == [A_WAV] * THREE_SENTENCES
    assert run.events[-1] == "done"


@pytest.mark.parametrize("case", SPEAK_FAILURES)
def test_every_speak_failure_keeps_what_was_spoken_and_ends_with_the_plain_error(
    tmp_path: Path, case: str
) -> None:
    service = FakeSpeechService(speak_answers=[a_chunked_wav, SPEAK_FAILURES[case]])
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz."), service=service
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.OK
    assert _spoken_wavs(run.response.text) == [A_WAV]
    assert run.events[-1] == "error"
    assert run.error_payload() == _THE_PLAIN_ERROR


@pytest.mark.parametrize(
    "peer",
    [
        pytest.param(RawPeer(MALFORMED_PEERS[name]).serving, id=name)
        for name in MALFORMED_PEERS
    ]
    + [pytest.param(nobody_listens, id="nobody-listens")],
)
def test_a_service_answering_nothing_the_client_can_read_ends_with_the_plain_error(
    tmp_path: Path, peer: Served
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(peer=peer)) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.OK
    assert run.events[-1] == "error"
    assert run.error_payload() == _THE_PLAIN_ERROR


def test_a_speak_past_its_deadline_ends_with_the_plain_error(tmp_path: Path) -> None:
    scenario = SpeechScenario(bounded_calls_stall=True)
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.events[-1] == "error"
    assert run.error_payload() == _THE_PLAIN_ERROR


def test_an_answer_ending_in_only_punctuation_speaks_the_rest_and_ends_in_a_plain_error(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    scenario = SpeechScenario(gateway=_say("Erster Satz. …"), service=service)
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.events.count("audio") == 1
    assert len(service.speak_requests) == 1
    assert run.error_payload() == _THE_PLAIN_ERROR
    assert "…" not in everything_shown(run.logged, instance.errors)


def _who_status(instance: SpeechInstance) -> HTTPStatus:
    response, _logged = who_over(instance)
    return HTTPStatus(response.status_code)


def test_who_says_a_ready_service_hears_at_the_clients_rate(tmp_path: Path) -> None:
    with a_speech_instance(tmp_path) as instance:
        response, _logged = who_over(instance)

    assert response.status_code == HTTPStatus.OK
    speech = response.json()["speech"]
    assert speech["hearing"]["ready"] is True
    assert speech["sample_rate"] == wire.SessionInput().format.rate


def test_who_says_hearing_still_loading_even_when_the_voice_is_not_ready(
    tmp_path: Path,
) -> None:
    body = a_health_document(hearing_ready=False, voice_ready=False)
    service = FakeSpeechService(health_answer=lambda: JSONResponse(body))
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        response, _logged = who_over(instance)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["speech"]["hearing"]["ready"] is False


@pytest.mark.parametrize("case", HEALTH_FAILURES)
def test_who_answers_503_for_every_health_that_cannot_say_ready(
    tmp_path: Path, case: str
) -> None:
    service = FakeSpeechService(health_answer=HEALTH_FAILURES[case])
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        status = _who_status(instance)

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize(
    "peer",
    [
        pytest.param(RawPeer(MALFORMED_PEERS[name]).serving, id=name)
        for name in MALFORMED_PEERS
    ]
    + [pytest.param(nobody_listens, id="nobody-listens")],
)
def test_who_answers_503_for_a_service_it_cannot_read(
    tmp_path: Path, peer: Served
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(peer=peer)) as instance:
        status = _who_status(instance)

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


def test_who_answers_503_for_a_health_past_its_deadline(tmp_path: Path) -> None:
    scenario = SpeechScenario(bounded_calls_stall=True)
    with a_speech_instance(tmp_path, scenario) as instance:
        status = _who_status(instance)

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_ends_the_ask_in_503_before_any_stream(
    tmp_path: Path, error: str
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(error))
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert not instance.service.speak_requests
    assert PLANTED not in everything_shown(run.logged, instance.errors)
    assert run.response.json() == {"detail": "Co-presenter unavailable"}


def test_one_ask_makes_two_refresh_grants_one_workload_grant_whatever_the_sentences(
    tmp_path: Path,
) -> None:
    scenario = SpeechScenario(gateway=_say("Eins. Zwei. Drei."))
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.grants_since_sign_in == [
        "refresh_token",
        "refresh_token",
        "client_credentials",
    ]
    assert instance.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]


def test_an_ask_refused_at_the_groups_read_makes_no_workload_grant(
    tmp_path: Path,
) -> None:
    scenario = SpeechScenario(arrange=the_refresh_token_is_revoked)
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.UNAUTHORIZED
    assert instance.hosts.issuer.workload_scopes == []


def _at_the_second_refresh(hosts: Hosts, act: Callable[[Hosts], None]) -> None:
    seen: list[int] = []

    def hook() -> None:
        if hosts.issuer.grant_types[-1] == "refresh_token":
            seen.append(1)
            if len(seen) == SECOND:
                act(hosts)

    hosts.issuer.during_token_request = hook


def _revoke_rotated(hosts: Hosts) -> None:
    hosts.issuer.revoke_refresh_token(ROTATED)


def _end_the_session(hosts: Hosts) -> None:
    delete_the_sessions(hosts)


def _take_the_issuer_down(hosts: Hosts) -> None:
    hosts.issuer.token_endpoint_is_down = True


def _raise_the_token_in_the_thread(_hosts: Hosts) -> None:
    raise RuntimeError(ACCESS)


@pytest.mark.parametrize(
    ("act", "status", "detail"),
    [
        (_revoke_rotated, HTTPStatus.UNAUTHORIZED, "Sign in again"),
        (_end_the_session, HTTPStatus.UNAUTHORIZED, "Not authenticated"),
        (
            _take_the_issuer_down,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
        ),
        (
            _raise_the_token_in_the_thread,
            HTTPStatus.SERVICE_UNAVAILABLE,
            "Co-presenter unavailable",
        ),
    ],
    ids=["revoked", "session-ended", "issuer-down", "thread-raises"],
)
def test_a_refusal_at_the_speech_read_answers_before_the_stream_and_frees_the_ticket(
    tmp_path: Path, act: Callable[[Hosts], None], status: HTTPStatus, detail: str
) -> None:
    with a_speech_instance(tmp_path) as instance:
        _at_the_second_refresh(instance.hosts, act)
        run = ask_over(instance)
        spoken_by_then = len(instance.service.speak_requests)
        again = ask_over(instance)

    assert run.response.status_code == status
    assert run.response.json() == {"detail": detail}
    assert spoken_by_then == 0
    assert again.response.status_code != HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.parametrize("scope", ["speech-scope-one", "another-speech-audience"])
def test_both_hosts_sign_in_naming_exactly_the_configured_speech_scope(
    tmp_path: Path, scope: str
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(speech_scope=scope)) as instance:
        lobby = instance.hosts.browser(A_BROWSERS_ADDRESS)
        talk = instance.hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        locations = [
            browser.get("/login/oidc").headers["location"] for browser in (lobby, talk)
        ]

    for location in locations:
        requested = parse_qs(urlsplit(location).query)["scope"][0].split()
        assert sorted(requested) == sorted(["openid", "profile", "groups", scope])


def test_a_browser_that_leaves_mid_answer_starts_no_later_speak_and_frees_the_ticket(
    tmp_path: Path,
) -> None:
    arrived = threading.Event()
    release = threading.Event()
    service = FakeSpeechService(speak_answers=[a_held_wav(arrived, release)])
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz. Dritter."), service=service
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        asyncio.run(ask_and_leave_at_the_first_speak(instance, arrived, release))
        spoken_when_it_left = len(service.speak_requests)
        again = ask_over(instance)

    assert spoken_when_it_left == 1
    assert again.response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("case", LEAK_ASKS)
def test_an_ask_shows_no_marker_in_any_log_error_answer_or_stored_row(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_ASKS[case]()) as instance:
        run = ask_over(instance)
        assert_nothing_shows(instance, run.logged, response=run.response)


@pytest.mark.parametrize("case", LEAK_HEALTH)
def test_a_health_read_shows_no_marker_in_any_log_error_or_answer(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_HEALTH[case]()) as instance:
        response, logged = who_over(instance)
        assert_nothing_shows(instance, logged, response=response)
