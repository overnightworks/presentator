"""The talk host's co-presenter routes over the real speech adapters."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing, suppress
from http import HTTPStatus
from typing import TYPE_CHECKING, Final
from urllib.parse import parse_qs, urlsplit

import pytest
from agent_runtime_client.backend.fake_gateway import FakeGateway, completion
from fastapi import Response, WebSocket
from fastapi.responses import JSONResponse
from speech_runtime_client import wire
from starlette.websockets import WebSocketDisconnect

from tests.support.copresenter_support import (
    ACCESS,
    API_MEMBER_GROUPS,
    HEARING,
    REFRESH,
    ROTATED,
    SPOKEN_QUESTION,
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
    WORKLOAD,
    SpeechInstance,
    SpeechScenario,
    a_speech_instance,
    ask_over,
    recorded_logs,
    session_columns,
)
from tests.support.speech_service import (
    A_WAV,
    FakeSpeechService,
    Hear,
    a_chunked_wav,
    a_health_document,
    a_refusal,
    reading,
)
from tests.support.talk_support import A, Hosts

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ORIGIN: Final = {"origin": A_TALK_ADDRESS}
HEARD: Final = "die gehörten Worte MARKER"
PLANTED: Final = "planted-MARKER"
_THREAD_WAIT_SECONDS: Final = 10
_POLICY_VIOLATION: Final = 1008
THREE_SENTENCES: Final = 3
SECOND: Final = 2


def _say(text: str) -> Callable[[], FakeGateway]:
    return lambda: FakeGateway(completion(text))


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
    assert run.events.count("audio") == THREE_SENTENCES
    assert A_WAV.hex() not in run.response.text
    assert run.events[-1] == "done"


@pytest.mark.parametrize(
    "answer",
    [a_refusal(wire.RefusalCode.UNAUTHORIZED), a_refusal(wire.RefusalCode.FORBIDDEN)],
    ids=["unauthorized", "forbidden"],
)
def test_a_refused_sentence_keeps_what_was_spoken_and_ends_with_a_plain_error(
    tmp_path: Path, answer: Callable[[], Response]
) -> None:
    service = FakeSpeechService(speak_answers=[a_chunked_wav, answer])
    scenario = SpeechScenario(
        gateway=_say("Erster Satz. Zweiter Satz."), service=service
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.events.count("audio") == 1
    assert run.events[-1] == "error"
    assert run.error_payload() == {"message": "the answer could not be spoken"}


def test_an_answer_ending_in_only_punctuation_speaks_the_rest_and_ends_in_a_plain_error(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    scenario = SpeechScenario(gateway=_say("Erster Satz. …"), service=service)
    with a_speech_instance(tmp_path, scenario) as instance:
        run = ask_over(instance)

    assert run.events.count("audio") == 1
    assert len(service.speak_requests) == 1
    assert run.error_payload() == {"message": "the answer could not be spoken"}
    assert "…" not in everything_shown(run.logged, instance.errors)


def test_the_who_route_reads_the_real_service(tmp_path: Path) -> None:
    def who_is(answer: Callable[[], Response], folder: str) -> tuple[int, object]:
        service = FakeSpeechService(health_answer=answer)
        subdirectory = tmp_path / folder
        subdirectory.mkdir()
        with a_speech_instance(
            subdirectory, SpeechScenario(service=service)
        ) as instance:
            response = instance.talk.get("/copresenter/who", params={"deck": "a-deck"})
        body = response.json() if response.status_code == HTTPStatus.OK else None
        return response.status_code, body

    def good() -> Response:
        return JSONResponse(a_health_document())

    def voice_missing() -> Response:
        return JSONResponse(a_health_document(voice="another-voice"))

    def loading() -> Response:
        return JSONResponse(a_health_document(hearing_ready=False, voice_ready=False))

    ok, ready = who_is(good, "ready")
    assert ok == HTTPStatus.OK
    assert ready["speech"]["hearing"]["ready"] is True  # type: ignore[index]
    assert ready["speech"]["sample_rate"] == wire.SessionInput().format.rate  # type: ignore[index]

    status, _ = who_is(voice_missing, "voice-missing")
    assert status == HTTPStatus.SERVICE_UNAVAILABLE

    loading_status, still = who_is(loading, "loading")
    assert loading_status == HTTPStatus.OK
    assert still["speech"]["hearing"]["ready"] is False  # type: ignore[index]


def _refuse_all_workload_grants(error: str) -> Callable[[Hosts], None]:
    def arrange(hosts: Hosts) -> None:
        hosts.issuer.workload_refusal = (error, PLANTED)

    return arrange


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_ends_the_ask_in_503_before_any_stream(
    tmp_path: Path, error: str
) -> None:
    scenario = SpeechScenario(arrange=_refuse_all_workload_grants(error))
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


def test_both_hosts_sign_in_naming_exactly_the_configured_speech_scope(
    tmp_path: Path,
) -> None:
    with a_speech_instance(tmp_path) as instance:
        expected = {"openid", "profile", "groups", A_SPEECH_SCOPE}
        lobby = instance.hosts.browser(A_BROWSERS_ADDRESS)
        talk = instance.hosts.browser(A_TALK_ADDRESS, headers=A_BROWSERS_HEADERS)
        for browser in (lobby, talk):
            location = browser.get("/login/oidc").headers["location"]
            scope = parse_qs(urlsplit(location).query)["scope"][0]
            assert set(scope.split()) == expected


def _release_the_recheck(instance: SpeechInstance) -> None:
    assert instance.deadline.started.wait(_THREAD_WAIT_SECONDS)
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
        release.wait(_THREAD_WAIT_SECONDS)

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
            assert entered.wait(_THREAD_WAIT_SECONDS)
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
            assert entered.wait(_THREAD_WAIT_SECONDS)
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
            assert entered.wait(_THREAD_WAIT_SECONDS)
            socket.send_bytes(b"second")
            socket.send_bytes(b"third")
            release.set()
            for _ in range(4):
                assert service.arrived.acquire(timeout=_THREAD_WAIT_SECONDS)

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
            assert entered.wait(_THREAD_WAIT_SECONDS)
            socket.send_bytes(b"\0" * (3 * 1024 * 1024))
            frame = socket.receive_json()
            release.set()
    assert frame == {"text": "", "final": True, "error": "hearing unavailable"}
    assert not service.upgrades


def test_session_end_closes_the_lease_and_the_service_socket_within_one_recheck(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(b"first")
            assert service.connected.wait(_THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
        assert service.arrived.acquire(timeout=_THREAD_WAIT_SECONDS)
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
            assert service.connected.wait(_THREAD_WAIT_SECONDS)
            two.send_bytes(b"second")
            assert two.receive_json() == {
                "text": "",
                "final": True,
                "error": "hearing unavailable",
            }


def _a_marked_refusal() -> Response:
    return Response(
        content=json.dumps({"error": {"message": PLANTED, "type": "server_error"}}),
        status_code=500,
        headers={"X-Marker": PLANTED},
    )


def _an_error_carrying_the_marker() -> Hear:
    async def realtime(connection: WebSocket) -> None:
        await connection.accept()
        await connection.receive()
        event = wire.ErrorEvent(
            error=wire.RealtimeError(
                message=PLANTED,
                type="server_error",
                code=wire.RefusalCode.UNAVAILABLE,
                param=None,
                event_id=None,
            )
        )
        await connection.send_text(event.model_dump_json())
        await connection.receive()

    return realtime


def _refused_upgrade_with_the_marker() -> Hear:
    async def realtime(connection: WebSocket) -> None:
        await connection.send_denial_response(
            Response(content=PLANTED, status_code=429, headers={"X-Marker": PLANTED})
        )

    return realtime


LEAK_ASKS: dict[str, SpeechScenario] = {
    "good": SpeechScenario(),
    "marked-refusal": SpeechScenario(
        service=FakeSpeechService(speak_answers=[_a_marked_refusal])
    ),
    "marked-issuer-refusal": SpeechScenario(
        arrange=_refuse_all_workload_grants(PLANTED)
    ),
    "punctuation-only": SpeechScenario(gateway=_say("Erster Satz. …")),
}

LEAK_HEARINGS: dict[str, Callable[[], FakeSpeechService]] = {
    "good": FakeSpeechService,
    "error-event": lambda: FakeSpeechService(realtime=_an_error_carrying_the_marker()),
    "marked-upgrade-refusal": lambda: FakeSpeechService(
        realtime=_refused_upgrade_with_the_marker()
    ),
}


def _shown_markers(instance: SpeechInstance) -> tuple[str, ...]:
    return (
        ACCESS,
        WORKLOAD,
        REFRESH,
        ROTATED,
        *instance.hosts.issuer.issued_id_tokens,
        "Bearer ",
        "eyJ",
        A.id,
        *API_MEMBER_GROUPS,
        str(SPOKEN_QUESTION["said"]),
        PLANTED,
    )


@pytest.mark.parametrize("case", LEAK_ASKS)
def test_an_ask_shows_no_secret_or_personal_word_in_any_log_error_or_sse(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_ASKS[case]) as instance:
        run = ask_over(instance)
        shown = everything_shown(run.logged, instance.errors)
        markers = _shown_markers(instance)
        rows = session_columns(instance.hosts)

    for marker in markers:
        assert marker not in shown
        assert marker not in run.response.text
        assert all(
            marker not in str(value) for column in rows.values() for value in column
        )


def _hear_recording(
    instance: SpeechInstance,
) -> tuple[list[dict[str, object]], list[object]]:
    frames: list[dict[str, object]] = []
    with (
        recorded_logs() as logged,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(b"first")
        with suppress(WebSocketDisconnect):
            frames.append(socket.receive_json())
    return frames, list(logged)


@pytest.mark.parametrize("case", LEAK_HEARINGS)
def test_a_hearing_shows_no_secret_or_personal_word_in_any_log_error_or_frame(
    tmp_path: Path, case: str
) -> None:
    service = LEAK_HEARINGS[case]()
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        frames, logged = _hear_recording(instance)
        shown = everything_shown(logged, instance.errors)  # type: ignore[arg-type]
        markers = _shown_markers(instance)

    for marker in markers:
        assert marker not in shown
        assert all(marker not in json.dumps(frame) for frame in frames)
