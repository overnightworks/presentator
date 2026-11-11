"""The hearing route through the real adapter and client, against the fake service."""

from __future__ import annotations

import base64
import logging
import sqlite3
import threading
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from fastapi import Response, WebSocket
from speech_client import wire
from speech_client.relay import HOP_FRAME_BYTES, HopClose
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
from tests.support.lobby import A_SPEECH_SCOPE, A_TALK_ADDRESS
from tests.support.speech_drive import (
    BROWSER_ENDINGS,
    LEAK_HEARINGS,
    ORIGIN,
    THREAD_WAIT_SECONDS,
    SpeechInstance,
    SpeechScenario,
    a_speech_instance,
    assert_nothing_shows,
    hear_and_record,
    recorded_logs,
    refusing_every_workload_grant,
)
from tests.support.speech_service import (
    MALFORMED_PEERS,
    PLANTED,
    FakeSpeechService,
    RawPeer,
    Served,
    a_failed_item,
    a_loopback_server,
    a_served_origin,
    accept_and_stay_silent,
    an_error_event,
    denying,
    never_answers,
    nobody_listens,
    reading,
    sending_then_reading,
)
from tests.support.talk_support import Hosts

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_POLICY_VIOLATION = 1008
_SHORT_SECONDS = 0.5
_THE_FIRST_FRAME = b"first"
_OVER_THE_HELD_LIMIT = (2 * 1024 * 1024) // HOP_FRAME_BYTES + 2


def _warnings(logged: list[logging.LogRecord]) -> list[str]:
    return [
        record.getMessage()
        for record in logged
        if record.levelno >= logging.WARNING and record.name.startswith("presentator")
    ]


def _appended(service: FakeSpeechService) -> list[bytes]:
    return [
        base64.b64decode(str(event["audio"]))
        for event in service.events
        if event["type"] == "input_audio_buffer.append"
    ]


def _reading_scenario(service: FakeSpeechService) -> SpeechScenario:
    service.realtime = reading(service)
    return SpeechScenario(service=service)


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


def _closed_with(socket: object) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_json()  # type: ignore[attr-defined]
    return closed.value


@pytest.mark.parametrize("case", BROWSER_ENDINGS)
def test_the_browser_gets_each_transcript_then_the_close_the_service_ended_with(
    tmp_path: Path, case: str
) -> None:
    ending = BROWSER_ENDINGS[case]
    service = FakeSpeechService(realtime=ending.hear)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == ending.frames
    assert heard.closed_with == ending.close
    if ending.close == HopClose.NORMAL:
        assert _warnings(heard.logged) == []


@pytest.mark.parametrize(
    ("peer", "named"),
    [
        pytest.param(
            RawPeer(MALFORMED_PEERS["status-line-not-http"]).serving,
            "Malformed",
            id="status-line-not-http",
        ),
        pytest.param(
            RawPeer(MALFORMED_PEERS["cut-header-block"]).serving,
            "Cut",
            id="cut-header-block",
        ),
        pytest.param(
            RawPeer(MALFORMED_PEERS["marked-status-reason"]).serving,
            "Refused status=500",
            id="marked-status-reason",
        ),
        pytest.param(
            RawPeer(b"HTTP/1.1 204 No Content\r\n\r\n").serving,
            "Malformed",
            id="no-upgrade-204",
        ),
        pytest.param(nobody_listens, "Unreachable", id="nobody-listens"),
    ],
)
def test_a_service_that_answers_the_upgrade_off_the_wire_closes_the_browser_4502(
    tmp_path: Path, peer: Served, named: str
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(peer=peer)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert _warnings(heard.logged) == [f"hearing failed: {named}"]


@pytest.mark.parametrize(
    ("status", "named"),
    [(403, "Refused status=403"), (503, "Refused status=503")],
)
def test_a_refused_upgrade_logs_one_warning_naming_its_type_and_status(
    tmp_path: Path, status: int, named: str
) -> None:
    service = FakeSpeechService(realtime=denying(status))
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)

    (warning,) = _warnings(heard.logged)
    assert named in warning
    assert heard.closed_with == HopClose.SERVICE_FAILED


def test_a_redirect_from_the_service_is_not_followed(tmp_path: Path) -> None:
    elsewhere = FakeSpeechService()
    with a_served_origin(lambda: a_loopback_server(elsewhere.app())) as other:
        redirecting = denying(307, headers={"Location": f"{other}{wire.REALTIME_PATH}"})
        service = FakeSpeechService(realtime=redirecting)
        with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
            heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not elsewhere.upgrades


@pytest.mark.parametrize(
    "event", [a_failed_item(), an_error_event()], ids=lambda e: type(e).__name__
)
def test_a_failed_item_or_error_event_closes_the_browser_4502_and_the_service_socket(
    tmp_path: Path, event: wire.ServerEvent
) -> None:
    service = FakeSpeechService()
    service.realtime = sending_then_reading(service, event)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)
        assert service.closed.wait(THREAD_WAIT_SECONDS)

    (warning,) = _warnings(heard.logged)
    assert type(event).__name__ in warning
    assert heard.closed_with == HopClose.SERVICE_FAILED


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_upgrade_carries_the_issued_tokens_and_names_the_language_before_audio(
    tmp_path: Path, language: str
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(tmp_path, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(
            HEARING.replace("language=de", f"language={language}"), headers=ORIGIN
        ) as socket,
    ):
        socket.send_bytes(_THE_FIRST_FRAME)
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


def test_a_language_the_wire_does_not_name_closes_4502_with_no_upgrade_and_one_warning(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(tmp_path, _reading_scenario(service)) as instance:
        heard = hear_and_record(instance, url=HEARING.replace("=de", "=fr"))

    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert len(_warnings(heard.logged)) == 1
    assert not service.upgrades


def test_frames_sent_during_the_token_read_reach_the_service_in_order_once_it_opens(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(tmp_path, _reading_scenario(service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            assert entered.wait(THREAD_WAIT_SECONDS)
            for frame in (b"first", b"second", b"third"):
                socket.send_bytes(frame)
            release.set()
            for _ in range(4):
                assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    assert _appended(service) == [b"first", b"second", b"third"]


def test_a_logout_during_the_token_read_closes_1008_with_no_upgrade_and_no_audio(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(tmp_path, _reading_scenario(service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket:
            socket.send_bytes(_THE_FIRST_FRAME)
            assert entered.wait(THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            closed = _closed_with(socket)
            release.set()

    assert closed.code == _POLICY_VIOLATION
    assert not service.upgrades


def test_a_sign_out_during_a_hearing_closes_the_browser_1000_and_the_service_socket(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(tmp_path, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(_THE_FIRST_FRAME)
        assert service.connected.wait(THREAD_WAIT_SECONDS)
        delete_the_sessions(instance.hosts)
        _release_the_recheck(instance)
        closed = _closed_with(socket)
        assert service.closed.wait(THREAD_WAIT_SECONDS)
        socket.send_bytes(b"after the close")
        sent_before_the_close = len(service.events)

    assert closed.code == HopClose.NORMAL
    assert service.closes == ["closed"]
    assert b"after the close" not in _appended(service)
    assert len(service.events) == sent_before_the_close


@pytest.mark.parametrize("error", ["invalid_scope", "invalid_client"])
def test_a_refused_workload_grant_closes_4502_with_no_upgrade_and_logs_its_reason(
    tmp_path: Path, error: str
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(error))
    with a_speech_instance(tmp_path, scenario) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not instance.service.upgrades
    shown = everything_shown(heard.logged)
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
def test_any_refused_token_read_closes_4502_with_no_upgrade(
    tmp_path: Path, arrange: Callable[[Hosts], None]
) -> None:
    with a_speech_instance(tmp_path, SpeechScenario(arrange=arrange)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not instance.service.upgrades


def test_one_hearing_makes_one_refresh_grant_and_one_workload_grant(
    tmp_path: Path,
) -> None:
    with a_speech_instance(tmp_path) as instance:
        before = len(instance.hosts.issuer.grant_types)
        hear_and_record(instance)
        grants = instance.hosts.issuer.grant_types[before:]

    assert grants == ["refresh_token", "client_credentials"]
    assert instance.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]


def test_a_second_hearing_the_service_finds_busy_closes_4429_on_the_second_only(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    first = reading(service)

    async def realtime(connection: WebSocket) -> None:
        if len(service.upgrades) > 1:
            await connection.send_denial_response(
                Response(status_code=429, headers={wire.RETRY_AFTER_HEADER: "3"})
            )
            return
        await first(connection)

    service.realtime = realtime
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        second_browser = instance.hosts.browser_with_the_cookies_of(
            instance.talk, A_TALK_ADDRESS
        )
        with (
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as one,
            second_browser.websocket_connect(HEARING, headers=ORIGIN) as two,
        ):
            one.send_bytes(_THE_FIRST_FRAME)
            assert service.connected.wait(THREAD_WAIT_SECONDS)
            for _ in range(2):
                assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)
            two.send_bytes(b"second")
            second = _closed_with(two)
            one.send_bytes(b"still hearing")
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    assert second.code == HopClose.BUSY
    assert _appended(service) == [_THE_FIRST_FRAME, b"still hearing"]


def test_a_text_frame_closes_4400_and_no_more_audio_reaches_the_service(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(tmp_path, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(_THE_FIRST_FRAME)
        for _ in range(2):
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)
        socket.send_text("not pcm")
        socket.send_bytes(b"after the text")
        closed = _closed_with(socket)
        assert service.closed.wait(THREAD_WAIT_SECONDS)

    assert closed.code == HopClose.TEXT_FRAME
    assert _appended(service) == [_THE_FIRST_FRAME]


def test_a_frame_over_the_hop_limit_closes_4413_and_one_at_the_limit_is_forwarded(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    at_the_limit = b"\0" * HOP_FRAME_BYTES
    with (
        a_speech_instance(tmp_path, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        socket.send_bytes(at_the_limit)
        for _ in range(2):
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)
        socket.send_bytes(at_the_limit + b"\0")
        closed = _closed_with(socket)
        assert service.closed.wait(THREAD_WAIT_SECONDS)

    assert closed.code == HopClose.FRAME_TOO_LARGE
    assert _appended(service) == [at_the_limit]


def test_audio_held_past_the_limit_before_the_service_opens_closes_4507(
    tmp_path: Path,
) -> None:
    scenario = SpeechScenario(peer=never_answers)
    with (
        a_speech_instance(tmp_path, scenario) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        for _ in range(_OVER_THE_HELD_LIMIT):
            socket.send_bytes(b"\0" * HOP_FRAME_BYTES)
        closed = _closed_with(socket)

    assert closed.code == HopClose.OVER_CAP


def test_a_hearing_reaching_its_deadline_closes_4504(tmp_path: Path) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    scenario = SpeechScenario(
        service=service, hearing_seconds=(_SHORT_SECONDS, THREAD_WAIT_SECONDS)
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.PAST_DEADLINE


@pytest.mark.parametrize(
    "silence",
    [
        pytest.param(never_answers, id="no-upgrade-answer"),
        pytest.param(None, id="no-first-event"),
    ],
)
def test_a_service_that_does_not_open_within_the_opening_bound_closes_4502(
    tmp_path: Path, silence: Served | None
) -> None:
    service = FakeSpeechService(realtime=accept_and_stay_silent)
    scenario = SpeechScenario(
        service=service,
        peer=silence,
        hearing_seconds=(THREAD_WAIT_SECONDS, _SHORT_SECONDS),
    )
    with a_speech_instance(tmp_path, scenario) as instance:
        heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.SERVICE_FAILED
    (warning,) = _warnings(heard.logged)
    assert "Unreachable" in warning


def test_a_browser_that_sends_nothing_opens_one_upgrade_and_one_session_update(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(tmp_path, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN),
    ):
        assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    (setup,) = service.events
    assert setup["type"] == "session.update"
    assert len(service.upgrades) == 1


def test_a_browser_that_leaves_mid_hearing_closes_the_service_socket_quietly(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    service.realtime = sending_then_reading(service)
    with a_speech_instance(tmp_path, SpeechScenario(service=service)) as instance:
        with (
            recorded_logs() as logged,
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
        ):
            socket.send_bytes(_THE_FIRST_FRAME)
            assert service.connected.wait(THREAD_WAIT_SECONDS)
        assert service.closed.wait(THREAD_WAIT_SECONDS)

    assert _warnings(list(logged)) == []
    assert not [record for record in logged if record.levelno >= logging.ERROR]


def test_a_browser_that_leaves_during_the_token_read_sends_no_audio_and_logs_no_error(
    tmp_path: Path,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(tmp_path, _reading_scenario(service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with (
            recorded_logs() as logged,
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
        ):
            socket.send_bytes(_THE_FIRST_FRAME)
            assert entered.wait(THREAD_WAIT_SECONDS)
        release.set()
        with closing(sqlite3.connect(instance.hosts.database)) as connection:
            stored = connection.execute("SELECT * FROM sessions").fetchone()
        if service.upgrades:
            assert service.closed.wait(THREAD_WAIT_SECONDS)

    assert ROTATED in stored
    assert _appended(service) == []
    assert not [record for record in logged if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("case", LEAK_HEARINGS)
def test_a_hearing_shows_no_marker_in_any_log_frame_close_or_stored_row(
    tmp_path: Path, case: str
) -> None:
    with a_speech_instance(tmp_path, LEAK_HEARINGS[case]()) as instance:
        heard = hear_and_record(instance)
        assert_nothing_shows(
            instance, heard.logged, frames=heard.frames, close_reasons=[heard.reason]
        )


def test_a_stalled_token_read_ended_by_logout_shows_no_marker(tmp_path: Path) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(PLANTED))
    with a_speech_instance(tmp_path, scenario) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with (
            recorded_logs() as logged,
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
        ):
            assert entered.wait(THREAD_WAIT_SECONDS)
            delete_the_sessions(instance.hosts)
            _release_the_recheck(instance)
            closed = _closed_with(socket)
            release.set()
        assert_nothing_shows(instance, list(logged), close_reasons=[closed.reason])
