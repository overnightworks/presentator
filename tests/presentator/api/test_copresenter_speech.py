"""The talk host's co-presenter routes over the real speech adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlsplit

import pytest
from agents.backend.fake_gateway import FakeGateway, completion
from fastapi.responses import JSONResponse
from logline import REQUEST_EVENT
from speech_client import wire
from sqlalchemy import Engine

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
from tests.support.log_lines import LogLine, json_logged
from tests.support.speech_drive import (
    LEAK_ASKS,
    LEAK_HEALTH,
    THE_ASKED_DECK,
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
from tests.support.talk_support import A, Hosts

if TYPE_CHECKING:
    from collections.abc import Callable


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
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz. Dritter."), service=service
    )
    with a_speech_instance(engine, scenario) as instance:
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
    engine: Engine, case: str
) -> None:
    service = FakeSpeechService(speak_answers=[a_chunked_wav, SPEAK_FAILURES[case]])
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz."), service=service
    )
    with a_speech_instance(engine, scenario) as instance:
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
    engine: Engine, peer: Served
) -> None:
    with a_speech_instance(engine, SpeechScenario(peer=peer)) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.OK
    assert run.events[-1] == "error"
    assert run.error_payload() == _THE_PLAIN_ERROR


def test_a_speak_past_its_deadline_ends_with_the_plain_error(engine: Engine) -> None:
    scenario = SpeechScenario(bounded_calls_stall=True)
    with a_speech_instance(engine, scenario) as instance:
        run = ask_over(instance)

    assert run.events[-1] == "error"
    assert run.error_payload() == _THE_PLAIN_ERROR


def test_an_answer_ending_in_only_punctuation_speaks_the_rest_and_ends_in_a_plain_error(
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    scenario = SpeechScenario(gateway=_say("Erster Satz. …"), service=service)
    with a_speech_instance(engine, scenario) as instance:
        run = ask_over(instance)

    assert run.events.count("audio") == 1
    assert len(service.speak_requests) == 1
    assert run.error_payload() == _THE_PLAIN_ERROR
    assert "…" not in everything_shown(run.logged, instance.errors)


def test_an_ask_writes_its_hops_and_its_answer_under_the_requests_trace(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = SpeechScenario(gateway=_say("Erster Satz. Zweiter."))
    with a_speech_instance(engine, scenario) as instance:
        capsys.readouterr()
        with json_logged(capsys) as logged:
            response = instance.talk.post("/copresenter/ask", json=THE_ASKED_DECK)

    assert response.status_code == HTTPStatus.OK
    by_event: dict[str, list[LogLine]] = {}
    for line in logged.lines:
        by_event.setdefault(str(line["event"]), []).append(line)
    (answer,) = by_event["answer.ended"]
    assert answer["outcome"] == "ok"
    assert isinstance(answer["first_output_ms"], float)
    assert [one["purpose"] for one in by_event["access_token.ended"]] == [
        "groups",
        "speech_tokens",
    ]
    assert len(by_event["speak.ended"]) == SECOND
    (request,) = by_event[REQUEST_EVENT]
    for event in ("access_token.ended", "speak.ended", "answer.ended"):
        assert all(one["trace_id"] == request["trace_id"] for one in by_event[event])
    for marker in (ACCESS, WORKLOAD, "Erster Satz"):
        assert marker not in logged.text


def test_a_failed_speech_call_ends_the_answer_as_a_warning_on_the_request_trace(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    service = FakeSpeechService(speak_answers=[SPEAK_FAILURES["cut-body"]])
    scenario = SpeechScenario(gateway=_say("Erster Satz."), service=service)
    with a_speech_instance(engine, scenario) as instance:
        capsys.readouterr()
        with json_logged(capsys) as logged:
            instance.talk.post("/copresenter/ask", json=THE_ASKED_DECK)

    (spoken,) = [one for one in logged.lines if one["event"] == "speak.ended"]
    (answer,) = [one for one in logged.lines if one["event"] == "answer.ended"]
    (request,) = [one for one in logged.lines if one["event"] == REQUEST_EVENT]
    assert (answer["level"], answer["outcome"], answer["reason"]) == (
        "warning",
        "failed",
        "unavailable",
    )
    assert spoken["trace_id"] == answer["trace_id"] == request["trace_id"]


def _who_status(instance: SpeechInstance) -> HTTPStatus:
    response, _logged = who_over(instance)
    return HTTPStatus(response.status_code)


def test_who_says_a_ready_service_is_ready_to_hear(engine: Engine) -> None:
    with a_speech_instance(engine) as instance:
        response, _logged = who_over(instance)

    assert response.status_code == HTTPStatus.OK
    speech = response.json()["speech"]
    assert speech["hearing"]["ready"] is True


def test_who_says_hearing_still_loading_even_when_the_voice_is_not_ready(
    engine: Engine,
) -> None:
    body = a_health_document(hearing_ready=False, voice_ready=False)
    service = FakeSpeechService(health_answer=lambda: JSONResponse(body))
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        response, _logged = who_over(instance)

    assert response.status_code == HTTPStatus.OK
    assert response.json()["speech"]["hearing"]["ready"] is False


@pytest.mark.parametrize("case", HEALTH_FAILURES)
def test_who_answers_503_for_every_health_that_cannot_say_ready(
    engine: Engine, case: str
) -> None:
    service = FakeSpeechService(health_answer=HEALTH_FAILURES[case])
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
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
    engine: Engine, peer: Served
) -> None:
    with a_speech_instance(engine, SpeechScenario(peer=peer)) as instance:
        status = _who_status(instance)

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


def test_who_answers_503_for_a_health_past_its_deadline(engine: Engine) -> None:
    scenario = SpeechScenario(bounded_calls_stall=True)
    with a_speech_instance(engine, scenario) as instance:
        status = _who_status(instance)

    assert status == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_ends_the_ask_in_503_before_any_stream(
    engine: Engine, error: str
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(error))
    with a_speech_instance(engine, scenario) as instance:
        run = ask_over(instance)

    assert run.response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert not instance.service.speak_requests
    assert PLANTED not in everything_shown(run.logged, instance.errors)
    assert run.response.json() == {"detail": "Co-presenter unavailable"}


def test_one_ask_makes_two_refresh_grants_one_workload_grant_whatever_the_sentences(
    engine: Engine,
) -> None:
    scenario = SpeechScenario(gateway=_say("Eins. Zwei. Drei."))
    with a_speech_instance(engine, scenario) as instance:
        run = ask_over(instance)

    assert run.grants_since_sign_in == [
        "refresh_token",
        "refresh_token",
        "client_credentials",
    ]
    assert instance.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]


def test_an_ask_refused_at_the_groups_read_makes_no_workload_grant(
    engine: Engine,
) -> None:
    scenario = SpeechScenario(arrange=the_refresh_token_is_revoked)
    with a_speech_instance(engine, scenario) as instance:
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
    engine: Engine, act: Callable[[Hosts], None], status: HTTPStatus, detail: str
) -> None:
    with a_speech_instance(engine) as instance:
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
    engine: Engine, scope: str
) -> None:
    with a_speech_instance(engine, SpeechScenario(speech_scope=scope)) as instance:
        lobby = instance.hosts.browser(A_BROWSERS_ADDRESS)
        talk = instance.hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        locations = [
            browser.get("/login/oidc").headers["location"] for browser in (lobby, talk)
        ]

    for location in locations:
        requested = parse_qs(urlsplit(location).query)["scope"][0].split()
        assert sorted(requested) == sorted(["openid", "profile", "groups", scope])


def test_a_browser_that_leaves_mid_answer_starts_no_later_speak_and_frees_the_ticket(
    engine: Engine,
) -> None:
    arrived = threading.Event()
    release = threading.Event()
    service = FakeSpeechService(speak_answers=[a_held_wav(arrived, release)])
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz. Dritter."), service=service
    )
    with a_speech_instance(engine, scenario) as instance:
        asyncio.run(ask_and_leave_at_the_first_speak(instance, arrived, release))
        spoken_when_it_left = len(service.speak_requests)
        again = ask_over(instance)

    assert spoken_when_it_left == 1
    assert again.response.status_code == HTTPStatus.OK


@pytest.mark.parametrize("case", LEAK_ASKS)
def test_an_ask_shows_no_marker_in_any_log_error_answer_or_stored_row(
    engine: Engine, case: str
) -> None:
    with a_speech_instance(engine, LEAK_ASKS[case]()) as instance:
        run = ask_over(instance)
        assert_nothing_shows(instance, run.logged, response=run.response)


@pytest.mark.parametrize("case", LEAK_HEALTH)
def test_a_health_read_shows_no_marker_in_any_log_error_or_answer(
    engine: Engine, case: str
) -> None:
    with a_speech_instance(engine, LEAK_HEALTH[case]()) as instance:
        response, logged = who_over(instance)
        assert_nothing_shows(instance, logged, response=response)


def _a_record(message: object) -> logging.LogRecord:
    return logging.LogRecord("probe", logging.INFO, __file__, 0, message, (), None)


def test_a_request_line_may_carry_the_admitted_persons_id_as_its_sub(
    engine: Engine,
) -> None:
    line = {"event": REQUEST_EVENT, "sub": A.id}
    with a_speech_instance(engine, next(iter(LEAK_ASKS.values()))()) as instance:
        assert_nothing_shows(instance, [_a_record(line)])


def test_a_request_line_whose_sub_is_any_other_value_is_still_checked(
    engine: Engine,
) -> None:
    leaking = _a_record({"event": REQUEST_EVENT, "sub": ACCESS})
    with (
        a_speech_instance(engine, next(iter(LEAK_ASKS.values()))()) as instance,
        pytest.raises(AssertionError),
    ):
        assert_nothing_shows(instance, [leaking])


def test_a_request_line_record_keeps_its_exception_text_when_its_sub_is_exempt(
    engine: Engine,
) -> None:
    line = {"event": REQUEST_EVENT, "sub": A.id}
    leaking = _a_record(line)
    leaking.exc_text = ACCESS
    with (
        a_speech_instance(engine, next(iter(LEAK_ASKS.values()))()) as instance,
        pytest.raises(AssertionError),
    ):
        assert_nothing_shows(instance, [leaking])
