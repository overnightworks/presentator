"""The talk host's co-presenter routes over the real speech adapters."""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sqlite3
import threading
from contextlib import closing
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlsplit

import pytest
from agent_runtime_client.backend.fake_gateway import FakeGateway, completion
from fastapi import Response, WebSocket
from fastapi.responses import JSONResponse
from speech_runtime_client import wire
from starlette.websockets import WebSocketDisconnect
from webauth.oidc import OidcRefusalReason

from tests.support.copresenter_support import (
    ACCESS,
    HEARING,
    ROTATED,
    delete_the_sessions,
    the_refresh_token_is_revoked,
    the_session_ends_during_the_grant,
    the_token_endpoint_goes_down,
    the_token_thread_fails,
)
from tests.support.leak_support import everything_shown
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_SPEECH_SCOPE,
    A_TALK_ADDRESS,
)
from tests.support.speech_drive import (
    BROWSER_FRAMES,
    LEAK_ASKS,
    LEAK_HEALTH,
    LEAK_HEARINGS,
    ORIGIN,
    THREAD_WAIT_SECONDS,
    WORKLOAD,
    SpeechInstance,
    SpeechScenario,
    a_speech_instance,
    ask_and_leave_at_the_first_speak,
    ask_over,
    assert_nothing_shows,
    hear_and_record,
    recorded_logs,
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
    reading,
)
from tests.support.talk_support import A, Hosts

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


_POLICY_VIOLATION: Final = 1008
THREE_SENTENCES: Final = 3
SECOND: Final = 2
_NO_HEARING: Final = {"text": "", "final": True, "error": "hearing unavailable"}
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    monkeypatch.setattr("tests.support.lobby.A_SPEECH_SCOPE", scope)
    with a_speech_instance(tmp_path) as instance:
        lobby = instance.hosts.browser(A_BROWSERS_ADDRESS)
        talk = instance.hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        locations = [
            browser.get("/login/oidc").headers["location"] for browser in (lobby, talk)
        ]

    for location in locations:
        requested = parse_qs(urlsplit(location).query)["scope"][0].split()
        assert sorted(requested) == sorted(["openid", "profile", "groups", scope])


def _release_the_recheck(instance: SpeechInstance) -> None:
    assert instance.deadline.started.wait(THREAD_WAIT_SECONDS)
    instance.deadline.release_first()


def _held_at_the_workload_grant(
    hosts: Hosts, *, then: Callable[[], None] = lambda: None
) -> tuple[threading.Event, threading.Event]:
    """Park the grant's thread at the issuer; return (entered, release)."""
    entered = threading.Event()
    release = threading.Event()

    def hold() -> None:
        entered.set()
        then()
        release.wait(THREAD_WAIT_SECONDS)

    hosts.issuer.during_workload_grant = hold
    return entered, release


def test_a_logout_during_the_grant_closes_the_lease_with_no_upgrade_and_no_audio(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert entered.wait(THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            release.set()
    assert closed.value.code == _POLICY_VIOLATION
    assert not service.upgrades


def test_a_browser_that_leaves_during_the_grant_ends_the_lease_with_no_upgrade(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert entered.wait(THREAD_WAIT_SECONDS)
        release.set()
        with closing(sqlite3.connect(instance.hosts.database)) as connection:
            stored = connection.execute("SELECT * FROM sessions").fetchone()
    assert ROTATED in stored
    assert not service.upgrades


def test_frames_sent_during_the_grant_reach_the_service_in_order_once_it_opens(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert entered.wait(THREAD_WAIT_SECONDS)
            socket.send_bytes(b"second")
            socket.send_bytes(b"third")
            release.set()
            for _ in range(4):
                assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    appended = [
        event["audio"]
        for event in service.events
        if event["type"] == "input_audio_buffer.append"
    ]
    assert appended == ["Zmlyc3Q=", "c2Vjb25k", "dGhpcmQ="]


def test_a_hold_past_the_bound_ends_the_hearing_in_no_hearing(tmp_path: Path) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert entered.wait(THREAD_WAIT_SECONDS)
            socket.send_bytes(b"\0" * (3 * 1024 * 1024))
            frame = socket.receive_json()
            release.set()
    assert frame == _NO_HEARING
    assert not service.upgrades


def test_a_first_frame_past_the_bound_ends_the_hearing_with_no_upgrade(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(b"\0" * (3 * 1024 * 1024))
        frame = socket.receive_json()
    assert frame == _NO_HEARING
    assert not service.upgrades


def test_session_end_closes_the_lease_and_the_service_socket_within_one_recheck(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert service.connected.wait(THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
        assert service.closed.wait(THREAD_WAIT_SECONDS)
    assert closed.value.code == _POLICY_VIOLATION
    assert service.closes == ["closed"]


def test_a_second_lease_the_service_refuses_shows_no_hearing_on_the_second_only(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    first = reading(service)

    async def realtime(connection: WebSocket) -> None:
        if len(service.upgrades) > 1:
            await connection.send_denial_response(Response(status_code=429))
            return
        await first(connection)

    service.realtime = realtime
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        second_browser = instance.hosts.signed_in_at(
            A_TALK_ADDRESS, A, headers=A_BROWSERS_HEADERS
        )
        with (
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as one,
            second_browser.websocket_connect(HEARING, headers=ORIGIN) as two,
        ):
            one.send_bytes(b"first")
            assert service.connected.wait(THREAD_WAIT_SECONDS)
            two.send_bytes(b"second")
            assert two.receive_json() == _NO_HEARING


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_upgrade_carries_the_issued_tokens_and_names_the_language_before_audio(
    tmp_path: Path, language: str
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with (
        a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance,
        instance.talk.websocket_connect(
            HEARING.replace("language=de", f"language={language}"), headers=ORIGIN
        ) as socket,
    ):
        socket.send_bytes(b"first")
        for _ in range(2):
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    (upgrade,) = service.upgrades
    issued = wire.token_headers(
        workload_token=instance.hosts.issuer.workload_token, person_token=ACCESS
    )
    for name, value in issued.items():
        assert upgrade.headers[name.lower()] == value
    assert service.upgrade_paths == [
        f"{wire.REALTIME_INTENT_PARAM}={wire.REALTIME_INTENT}"
    ]
    setup, append = service.events
    assert setup["type"] == "session.update"
    session_input = setup["session"]["audio"]["input"]  # type: ignore[index]
    assert session_input["transcription"]["language"] == language  # type: ignore[index]
    assert append["type"] == "input_audio_buffer.append"


def test_one_lease_makes_one_refresh_grant_and_one_workload_grant(
    tmp_path: Path,
) -> None:
    with a_speech_instance(tmp_path) as instance:
        before = len(instance.hosts.issuer.grant_types)
        frames, _logged = hear_and_record(instance)
        grants = instance.hosts.issuer.grant_types[before:]

    assert grants == ["refresh_token", "client_credentials"]
    assert instance.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]
    assert frames[-1] == _NO_HEARING


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_ends_the_hearing_in_no_hearing_and_logs_its_reason(
    tmp_path: Path, error: str
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(error))
    with a_speech_instance(tmp_path, scenario) as instance:
        frames, logged = hear_and_record(instance)

    assert frames == [_NO_HEARING]
    assert not instance.service.upgrades
    shown = everything_shown(logged)
    assert OidcRefusalReason.TOKEN_REQUEST_REFUSED.value in shown
    assert error not in shown


@pytest.mark.parametrize(
    "arrange",
    [
        the_refresh_token_is_revoked,
        the_session_ends_during_the_grant,
        the_token_endpoint_goes_down,
        the_token_thread_fails,
    ],
    ids=["revoked", "session-ended", "issuer-down", "thread-raises"],
)
def test_any_refused_token_read_ends_the_hearing_with_no_upgrade(
    tmp_path: Path, arrange: Callable[[Hosts], None]
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(arrange=arrange)) as instance:
        frames, _logged = hear_and_record(instance)

    assert frames == [_NO_HEARING]
    assert not instance.service.upgrades


@pytest.mark.parametrize("case", BROWSER_FRAMES)
def test_the_browser_gets_each_transcript_then_one_generic_no_hearing_frame(
    tmp_path: Path, case: str
) -> None:
    realtime, transcripts = BROWSER_FRAMES[case]
    service = FakeSpeechService(realtime=realtime)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        frames, _logged = hear_and_record(instance)

    assert frames == [*transcripts, _NO_HEARING]


@pytest.mark.parametrize(
    "peer",
    [
        pytest.param(RawPeer(MALFORMED_PEERS[name]).serving, id=name)
        for name in MALFORMED_PEERS
    ]
    + [
        pytest.param(
            RawPeer(b"HTTP/1.1 204 No Content\r\n\r\n").serving,
            id="no-upgrade-204",
        ),
        pytest.param(nobody_listens, id="nobody-listens"),
    ],
)
def test_a_service_that_answers_the_upgrade_no_useful_way_shows_no_hearing(
    tmp_path: Path, peer: Served
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(peer=peer)) as instance:
        frames, _logged = hear_and_record(instance)

    assert frames == [_NO_HEARING]


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


@pytest.mark.parametrize("case", LEAK_HEARINGS)
def test_a_hearing_shows_no_marker_in_any_log_error_frame_or_stored_row(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_HEARINGS[case]()) as instance:
        frames, logged = hear_and_record(instance)
        assert_nothing_shows(instance, logged, frames=frames)


@pytest.mark.parametrize("case", LEAK_HEALTH)
def test_a_health_read_shows_no_marker_in_any_log_error_or_answer(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_HEALTH[case]()) as instance:
        response, logged = who_over(instance)
        assert_nothing_shows(instance, logged, response=response)


def test_a_stalled_grant_ended_by_logout_shows_no_marker(tmp_path: Path) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(PLANTED))
    with a_speech_instance(tmp_path, scenario) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with (
            recorded_logs() as logged,
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
        ):
            socket.send_bytes(b"first")
            assert entered.wait(THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            release.set()
        assert_nothing_shows(
            instance, list(logged), close_reasons=[closed.value.reason]
        )
