"""The hearing route through the real adapter and client, against the fake service."""

from __future__ import annotations

import base64
import json
import logging
import threading
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import Response, WebSocket
from speech_client import realtime_wire, wire
from speech_client.relay import HOP_FRAME_BYTES, HopClose
from sqlalchemy import Engine, text
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
from tests.support.hearing_endings import BROWSER_ENDINGS
from tests.support.leak_support import everything_shown
from tests.support.lobby import A_SPEECH_SCOPE, A_TALK_ADDRESS
from tests.support.log_lines import LoggedText, LogLine, json_lines_among
from tests.support.speech_drive import (
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
    a_completed,
    a_delta,
    a_failed_item,
    a_loopback_server,
    a_served_origin,
    accept_and_stay_silent,
    an_error_event,
    denying,
    draining,
    never_answers,
    nobody_listens,
    one_after_another,
    reading,
    refusing,
    sending,
    sending_then_reading,
)
from tests.support.talk_support import Hosts

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.testclient import WebSocketTestSession

    from tests.support.speech_service import Hear

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


def _the_session_line(capsys: pytest.CaptureFixture[str]) -> LogLine:
    (line,) = [
        one
        for one in json_lines_among(LoggedText(capsys).text)
        if one["event"] == "hearing_session.ended"
    ]
    return line


def _hearing_lines(capsys: pytest.CaptureFixture[str]) -> str:
    """The hearing's own lines as written, without the request lines."""
    return json.dumps(
        [
            line
            for line in json_lines_among(LoggedText(capsys).text)
            if str(line["event"]).startswith("hearing_")
        ]
    )


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


def _closed_with(socket: WebSocketTestSession) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as closed:
        socket.receive_json()
    return closed.value


@pytest.mark.parametrize("case", BROWSER_ENDINGS)
def test_the_browser_gets_each_transcript_then_the_close_the_service_ended_with(
    engine: Engine, case: str
) -> None:
    ending = BROWSER_ENDINGS[case]
    service = FakeSpeechService(realtime=ending.hear())
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == ending.frames
    assert heard.closed_with == ending.close
    if ending.close == HopClose.NORMAL:
        assert _warnings(heard.logged) == []


def test_a_final_transcript_carries_the_trace_of_the_turn_the_service_heard(
    engine: Engine,
) -> None:
    turn = f"00-{'1' * 32}-{'2' * 16}-01"
    service = FakeSpeechService(
        realtime=sending(a_delta("Ha"), a_completed("Hallo.", traceparent=turn))
    )
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)

    assert [frame["traceparent"] for frame in heard.frames] == [None, turn]


@pytest.mark.parametrize(
    ("peer", "reason"),
    [
        pytest.param(
            RawPeer(MALFORMED_PEERS["status-line-not-http"]).serving,
            "malformed",
            id="status-line-not-http",
        ),
        pytest.param(
            RawPeer(MALFORMED_PEERS["cut-header-block"]).serving,
            "cut",
            id="cut-header-block",
        ),
        pytest.param(
            RawPeer(MALFORMED_PEERS["marked-status-reason"]).serving,
            "refused",
            id="marked-status-reason",
        ),
        pytest.param(
            RawPeer(b"HTTP/1.1 204 No Content\r\n\r\n").serving,
            "malformed",
            id="no-upgrade-204",
        ),
        pytest.param(nobody_listens, "unreachable", id="nobody-listens"),
    ],
)
def test_a_service_that_answers_the_upgrade_off_the_wire_closes_the_browser_4502(
    engine: Engine, peer: Served, reason: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with a_speech_instance(engine, SpeechScenario(peer=peer)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    session = _the_session_line(capsys)
    assert (session["outcome"], session["reason"]) == ("failed", reason)


@pytest.mark.parametrize(
    ("refusal", "status", "code"),
    [
        (denying(403), 403, None),
        (denying(503), 503, None),
        (
            refusing(wire.RefusalCode.FORBIDDEN),
            wire.REFUSAL_STATUS[wire.RefusalCode.FORBIDDEN],
            wire.RefusalCode.FORBIDDEN.value,
        ),
    ],
    ids=["unmarked-403", "unmarked-503", "coded"],
)
def test_a_refused_upgrade_logs_one_session_line_naming_its_refusal_status_and_code(
    engine: Engine,
    refusal: Hear,
    status: int,
    code: str | None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = FakeSpeechService(realtime=refusal)
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)

    session = _the_session_line(capsys)
    assert (session["reason"], session["status"], session.get("code")) == (
        "refused",
        status,
        code,
    )
    assert heard.closed_with == HopClose.SERVICE_FAILED


def test_a_redirect_from_the_service_is_not_followed(engine: Engine) -> None:
    elsewhere = FakeSpeechService()
    with a_served_origin(lambda: a_loopback_server(elsewhere.app())) as other:
        redirecting = denying(307, headers={"Location": f"{other}{wire.REALTIME_PATH}"})
        service = FakeSpeechService(realtime=redirecting)
        with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
            heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not elsewhere.upgrades


@pytest.mark.parametrize(
    "event", [a_failed_item(), an_error_event()], ids=lambda e: type(e).__name__
)
def test_a_failed_item_or_error_event_closes_the_browser_4502_and_the_service_socket(
    engine: Engine, event: realtime_wire.ServerEvent, capsys: pytest.CaptureFixture[str]
) -> None:
    service = FakeSpeechService()
    service.realtime = sending_then_reading(service, event)
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        heard = hear_and_record(instance)
        assert service.closed.wait(THREAD_WAIT_SECONDS)

    session = _the_session_line(capsys)
    assert (session["reason"], session["code"]) == (
        "service_failed",
        wire.RefusalCode.UNAVAILABLE.value,
    )
    assert heard.closed_with == HopClose.SERVICE_FAILED


@pytest.mark.parametrize("language", ["de", "en"])
def test_the_upgrade_carries_the_issued_tokens_and_names_the_language_before_audio(
    engine: Engine, language: str
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(engine, _reading_scenario(service)) as instance,
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
    transcription = cast("dict[str, list[str]]", session_input["transcription"])  # type: ignore[index]
    languages = transcription["languages"]
    assert languages[0] == language
    assert set(languages) == set(wire.Language)
    assert append["type"] == "input_audio_buffer.append"


def test_a_language_the_wire_does_not_name_closes_4502_with_no_upgrade_and_a_refusal(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(engine, _reading_scenario(service)) as instance:
        heard = hear_and_record(instance, url=HEARING.replace("=de", "=fr"))

    assert heard.closed_with == HopClose.SERVICE_FAILED
    session = _the_session_line(capsys)
    assert (session["outcome"], session["reason"]) == ("refused", "unknown_language")
    assert "language" not in session
    assert not service.upgrades


def test_frames_sent_during_the_token_read_reach_the_service_in_order_once_it_opens(
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(engine, _reading_scenario(service)) as instance:
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
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(engine, _reading_scenario(service)) as instance:
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
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(engine, _reading_scenario(service)) as instance,
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
    engine: Engine, error: str, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(error))
    with a_speech_instance(engine, scenario) as instance:
        heard = hear_and_record(instance)
    written = LoggedText(capsys).text

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not instance.service.upgrades
    shown = everything_shown(heard.logged)
    assert OidcRefusalReason.TOKEN_REQUEST_REFUSED.value in shown
    assert error not in shown
    assert error not in written
    assert OidcRefusalReason.TOKEN_REQUEST_REFUSED.value in written


@pytest.mark.parametrize(
    "arrange",
    [
        the_refresh_token_is_revoked,
        the_token_endpoint_goes_down,
        the_token_thread_fails,
    ],
    ids=["revoked", "issuer-down", "thread-raises"],
)
def test_any_refused_token_read_closes_4502_with_no_upgrade(
    engine: Engine, arrange: Callable[[Hosts], None]
) -> None:
    with a_speech_instance(engine, SpeechScenario(arrange=arrange)) as instance:
        heard = hear_and_record(instance)

    assert heard.frames == []
    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert not instance.service.upgrades


def _the_session_ends_at_the_reopening(hosts: Hosts) -> None:
    first_opening_refreshed = False

    def end_at_the_reopening_refresh() -> None:
        nonlocal first_opening_refreshed
        if hosts.issuer.grant_types[-1] != "refresh_token":
            return
        if first_opening_refreshed:
            delete_the_sessions(hosts)
        first_opening_refreshed = True

    hosts.issuer.during_token_request = end_at_the_reopening_refresh


@pytest.mark.parametrize(
    ("arrange", "upgrades"),
    [
        (the_session_ends_during_the_grant, 0),
        (_the_session_ends_at_the_reopening, 1),
    ],
    ids=["first-read", "reopening"],
)
def test_a_session_that_ends_as_the_hearing_reads_its_tokens_closes_1000(
    engine: Engine, arrange: Callable[[Hosts], None], upgrades: int
) -> None:
    service = FakeSpeechService(
        realtime=one_after_another(draining(), sending(a_completed("Wieder da.")))
    )
    scenario = SpeechScenario(service=service, arrange=arrange)
    with a_speech_instance(engine, scenario) as instance:
        heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.NORMAL
    assert len(service.upgrades) == upgrades


def test_one_hearing_makes_one_refresh_grant_and_one_workload_grant(
    engine: Engine,
) -> None:
    with a_speech_instance(engine) as instance:
        before = len(instance.hosts.issuer.grant_types)
        hear_and_record(instance)
        grants = instance.hosts.issuer.grant_types[before:]

    assert grants == ["refresh_token", "client_credentials"]
    assert instance.hosts.issuer.workload_scopes == [A_SPEECH_SCOPE]


def test_a_hearing_the_service_drained_reopens_on_tokens_granted_again(
    engine: Engine,
) -> None:
    service = FakeSpeechService(
        realtime=one_after_another(draining(), sending(a_completed("Wieder da.")))
    )
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        before = len(instance.hosts.issuer.grant_types)
        heard = hear_and_record(instance)
        grants = instance.hosts.issuer.grant_types[before:]

    assert heard.closed_with == HopClose.NORMAL
    assert len(service.upgrades) > 1
    assert grants == ["refresh_token", "client_credentials"] * len(service.upgrades)


def test_a_second_hearing_the_service_finds_busy_closes_4429_on_the_second_only(
    engine: Engine,
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
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
        second_browser = instance.hosts.browser_with_the_cookies_of(
            instance.talk, A_TALK_ADDRESS
        )
        with instance.talk.websocket_connect(HEARING, headers=ORIGIN) as one:
            one.send_bytes(_THE_FIRST_FRAME)
            assert service.connected.wait(THREAD_WAIT_SECONDS)
            for _ in range(2):
                assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)
            with second_browser.websocket_connect(HEARING, headers=ORIGIN) as two:
                two.send_bytes(b"second")
                second = _closed_with(two)
            one.send_bytes(b"still hearing")
            assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    assert second.code == HopClose.BUSY
    assert _appended(service) == [_THE_FIRST_FRAME, b"still hearing"]


def test_a_text_frame_closes_4400_and_no_more_audio_reaches_the_service(
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(engine, _reading_scenario(service)) as instance,
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
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    at_the_limit = b"\0" * HOP_FRAME_BYTES
    with (
        a_speech_instance(engine, _reading_scenario(service)) as instance,
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
    engine: Engine,
) -> None:
    scenario = SpeechScenario(peer=never_answers)
    with (
        a_speech_instance(engine, scenario) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
    ):
        for _ in range(_OVER_THE_HELD_LIMIT):
            socket.send_bytes(b"\0" * HOP_FRAME_BYTES)
        closed = _closed_with(socket)

    assert closed.code == HopClose.OVER_CAP


def test_a_hearing_reaching_its_deadline_closes_4504(engine: Engine) -> None:
    service = FakeSpeechService()
    service.realtime = reading(service)
    scenario = SpeechScenario(
        service=service, hearing_seconds=(_SHORT_SECONDS, THREAD_WAIT_SECONDS)
    )
    with a_speech_instance(engine, scenario) as instance:
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
    engine: Engine, silence: Served | None, capsys: pytest.CaptureFixture[str]
) -> None:
    service = FakeSpeechService(realtime=accept_and_stay_silent)
    scenario = SpeechScenario(
        service=service,
        peer=silence,
        hearing_seconds=(THREAD_WAIT_SECONDS, _SHORT_SECONDS),
    )
    with a_speech_instance(engine, scenario) as instance:
        heard = hear_and_record(instance)

    assert heard.closed_with == HopClose.SERVICE_FAILED
    assert _the_session_line(capsys)["reason"] == "unreachable"


def test_a_browser_that_sends_nothing_opens_one_upgrade_and_one_session_update(
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with (
        a_speech_instance(engine, _reading_scenario(service)) as instance,
        instance.talk.websocket_connect(HEARING, headers=ORIGIN),
    ):
        assert service.arrived.acquire(timeout=THREAD_WAIT_SECONDS)

    (setup,) = service.events
    assert setup["type"] == "session.update"
    assert len(service.upgrades) == 1


def test_a_browser_that_leaves_mid_hearing_closes_the_service_socket_quietly(
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    service.realtime = sending_then_reading(service)
    with a_speech_instance(engine, SpeechScenario(service=service)) as instance:
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
    engine: Engine,
) -> None:
    service = FakeSpeechService()
    with a_speech_instance(engine, _reading_scenario(service)) as instance:
        entered, release = _held_at_the_workload_grant(instance.hosts)
        with (
            recorded_logs() as logged,
            instance.talk.websocket_connect(HEARING, headers=ORIGIN) as socket,
        ):
            socket.send_bytes(_THE_FIRST_FRAME)
            assert entered.wait(THREAD_WAIT_SECONDS)
        release.set()
        with instance.hosts.database.connect() as connection:
            stored = connection.execute(text("SELECT * FROM sessions")).one()
        if service.upgrades:
            assert service.closed.wait(THREAD_WAIT_SECONDS)

    assert ROTATED in stored
    assert _appended(service) == []
    assert not [record for record in logged if record.levelno >= logging.WARNING]


@pytest.mark.parametrize("case", LEAK_HEARINGS)
def test_a_hearing_shows_no_marker_in_any_log_frame_close_or_stored_row(
    engine: Engine, case: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with a_speech_instance(engine, LEAK_HEARINGS[case]()) as instance:
        heard = hear_and_record(instance)
        assert_nothing_shows(
            instance,
            heard.logged,
            frames=heard.frames,
            texts=[heard.reason, _hearing_lines(capsys)],
        )


def test_a_stalled_token_read_ended_by_logout_shows_no_marker(
    engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = SpeechScenario(arrange=refusing_every_workload_grant(PLANTED))
    with a_speech_instance(engine, scenario) as instance:
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
        assert_nothing_shows(
            instance,
            list(logged),
            texts=[closed.reason, _hearing_lines(capsys)],
        )
