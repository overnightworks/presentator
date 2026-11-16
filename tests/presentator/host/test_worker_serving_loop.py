"""The serving loop admits only a verified hello and ends the life by name."""

import errno
import socket
import threading
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import NoReturn

import pytest

from presentator.adapters import build_wire
from presentator.host import worker, worker_self_check, worker_serving
from presentator.host.worker_serving import (
    HEADER_SECONDS,
    HelloRefusal,
    LifeEnd,
)
from presentator.host.worker_termination import SWEEP_SECONDS, SweepEffects, sweep
from presentator.host.worker_wire import WireExit
from tests.presentator.host.support.sealed_snapshot import sealed_snapshot, with_fields
from tests.support.log_lines import events_of, json_logged
from tests.support.step_support import ADMITTING_PATH, STEP_LEG
from tests.support.worker_support import (
    ACCEPT_TIME,
    ANSWERED_HEADER,
    CHILD_LEG,
    CLIENTS,
    CONNECTIONS,
    CUT_OFF_HEADER,
    IDLE_LEG,
    KEY,
    SENDER_LEG,
    STATUS,
    UNUSED_WORK,
    VALID_STEP,
    WAIT_SECONDS,
    BoundedListener,
    ScriptedLife,
    StopServingError,
    a_proven_hello,
    accept_from,
    connect_when_bound,
    helloing,
    listening_with,
    proven,
    reading_after_a_proven_hello,
    serving,
    wake_until_ended,
)


def connected_when_bound(
    volume: Path, still_serving: Callable[[], bool]
) -> socket.socket:
    return CLIENTS.enter_context(connect_when_bound(volume, still_serving))


def read_challenge(client: socket.socket) -> bytes:
    return client.recv(build_wire.CHALLENGE_BYTES, socket.MSG_WAITALL)[1:]


def answer_under(
    client: socket.socket, direction: build_wire.HelloDirection, key: bytes
) -> None:
    nonce = read_challenge(client)
    web_nonce = b"w" * build_wire.NONCE_BYTES
    tag = build_wire.hello_tag(key, direction, nonce, web_nonce)
    client.sendall(bytes([build_wire.HELLO_VERSION]) + web_nonce + tag)


def a_wrong_key_hello(client: socket.socket) -> None:
    answer_under(client, build_wire.HelloDirection.WEB_TO_WORKER, b"x" * 32)


def a_reflected_hello(client: socket.socket) -> None:
    answer_under(client, build_wire.HelloDirection.WORKER_TO_WEB, KEY)


def a_silent_hello(client: socket.socket) -> None:
    read_challenge(client)


REFUSED_HELLOS = {
    "wrong key": a_wrong_key_hello,
    "reflected direction": a_reflected_hello,
    "no answer": a_silent_hello,
}


@pytest.mark.parametrize("hello", REFUSED_HELLOS.values(), ids=REFUSED_HELLOS.keys())
def test_a_refused_hello_is_closed_and_never_spawns_a_child(
    volume: Path,
    monkeypatch: pytest.MonkeyPatch,
    hello: Callable[[socket.socket], None],
) -> None:
    monkeypatch.setattr(worker_serving, "HELLO_SECONDS", 0.2)
    life = ScriptedLife()
    with listening_with(volume, accepts=1) as listener:
        client = helloing(volume, hello)
        with pytest.raises(StopServingError):
            accept_from(life, listener)
        assert client.recv(1) == b""

    assert life.spawned == []


@pytest.mark.parametrize("hello", REFUSED_HELLOS.values(), ids=REFUSED_HELLOS.keys())
def test_a_refused_hello_writes_one_refused_connection_line_and_never_the_key(
    volume: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hello: Callable[[socket.socket], None],
) -> None:
    monkeypatch.setattr(worker_serving, "HELLO_SECONDS", 0.2)
    life = ScriptedLife()
    with (
        json_logged(capsys) as logged,
        listening_with(volume, accepts=1) as listener,
    ):
        helloing(volume, hello)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    (line,) = events_of(logged, "connection.ended")
    assert (line["outcome"], line["reason"]) == ("refused", HelloRefusal.NOT_PROVEN)
    assert KEY.hex() not in logged.text
    assert repr(KEY) not in logged.text


def test_a_refused_hello_spends_no_life_and_the_next_verified_one_is_served(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", ADMITTING_PATH)
    life = ScriptedLife()
    with listening_with(volume, accepts=2) as listener:
        helloing(volume, a_wrong_key_hello)
        helloing(volume, a_proven_hello)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    assert len(life.spawned) == 1
    assert life.events[:3] == ["arm 7.0", "sweep 104.0", "cancel"]


def test_a_refused_hello_is_swept_and_the_watchdog_cancelled_before_the_next_accept(
    volume: Path,
) -> None:
    life = ScriptedLife()
    with listening_with(volume, accepts=2) as listener:
        for _ in range(2):
            helloing(volume, a_wrong_key_hello)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    assert life.events == 2 * ["arm 7.0", "sweep 104.0", "cancel"]


def test_a_kept_life_connection_is_handed_to_a_gated_child_then_swept(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", ADMITTING_PATH)
    life = ScriptedLife(exit_status=WireExit.CLOSED)
    with listening_with(volume, accepts=2) as listener:
        reads = [
            reading_after_a_proven_hello(volume),
            reading_after_a_proven_hello(volume),
        ]
        with pytest.raises(StopServingError):
            accept_from(life, listener)
        assert [read() for read in reads] == [b"", b""]

    assert life.events == 2 * ANSWERED_HEADER
    assert [spawned.environment for spawned in life.spawned] == 2 * [
        {"PATH": ADMITTING_PATH}
    ]


def test_the_child_is_told_the_descriptor_of_its_connection_and_inherits_only_it(
    volume: Path,
) -> None:
    life = ScriptedLife()
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    [spawned] = life.spawned
    assert spawned.command[1:] == [
        "-I",
        "-m",
        "presentator.host.worker_wire",
        "--role",
        "idle",
        "--connection-fd",
        str(spawned.pass_fds[0]),
        "--record-fd",
        str(spawned.pass_fds[1]),
        "--work",
        str(UNUSED_WORK),
        "--megabytes",
        "300",
    ]


def test_a_sweep_that_is_quiet_only_after_its_deadline_ends_the_life_armed(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", ADMITTING_PATH)
    life = ScriptedLife()
    late = SweepEffects(
        kill_all=lambda: None,
        reap=lambda: None,
        list_process_ids=lambda: ["1"],
        clock=lambda: ACCEPT_TIME + SWEEP_SECONDS + 1,
        sleep=lambda _seconds: None,
    )
    late_serving = replace(life.serving(), sweep=partial(sweep, late))
    with listening_with(volume, accepts=2) as listener:
        proven(volume)
        proven(volume)
        outcome = accept_from(life, listener, late_serving)

    assert outcome == LifeEnd.SWEEP
    assert "cancel" not in life.events


ACCEPTED_REARMS = {
    "the design's 300 s build, observed at the accept": (300.0, 0.0, 447.0, 105.0),
    "the design's 1 s build, observed at the accept": (1.0, 0.0, 148.0, 105.0),
    "an observation 1.5 s after the accept shortens it by 1.5 s": (
        1.0,
        1.5,
        146.5,
        108.0,
    ),
}


@pytest.mark.parametrize(
    ("build_timeout", "inspection_takes", "rearmed_for", "quiesce_deadline"),
    ACCEPTED_REARMS.values(),
    ids=ACCEPTED_REARMS.keys(),
)
def test_an_accepted_step_re_arms_the_watchdog_to_the_emergency_end_from_the_accept(
    volume: Path,
    build_timeout: float,
    inspection_takes: float,
    rearmed_for: float,
    quiesce_deadline: float,
) -> None:
    life = ScriptedLife(
        exit_status=WireExit.ACCEPTED,
        build_timeout_seconds=build_timeout,
        inspection_takes=inspection_takes,
    )
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        accept_from(life, listener)

    assert life.events == [
        "arm 7.0",
        *IDLE_LEG,
        f"arm {rearmed_for}",
        "seal",
        "open-deck",
        *CHILD_LEG,
        "close-deck",
        *STEP_LEG,
        f"sweep {quiesce_deadline}",
        *SENDER_LEG,
    ]


def consume_an_accepted_step(volume: Path, life: ScriptedLife) -> None:
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        accept_from(life, listener)


def test_every_role_is_spawned_with_the_work_and_the_megabytes_of_the_serving(
    volume: Path,
) -> None:
    life = ScriptedLife(
        exit_status=WireExit.ACCEPTED, build_output_megabytes=7, work=Path("/a")
    )

    consume_an_accepted_step(volume, life)

    idle, upload, sender = life.spawned
    assert [spawned.command[-4:] for spawned in (idle, upload)] == [
        ["--work", "/a", "--megabytes", "7"]
    ] * 2
    assert sender.command[-6:] == ["--work", "/a", "--megabytes", "7", "--plan", "walk"]
    assert [
        spawned.command[spawned.command.index("--role") + 1] for spawned in life.spawned
    ] == [
        "idle",
        "upload",
        "send",
    ]


def test_the_deck_effects_receive_the_deck_below_the_work_root_of_the_serving(
    volume: Path,
) -> None:
    life = ScriptedLife(exit_status=WireExit.ACCEPTED, work=Path("/a"))

    consume_an_accepted_step(volume, life)

    assert life.decks_handled == [Path("/a/deck")] * 2


LIFE_ENDS = {
    "a spawn failure": (
        lambda: ScriptedLife(spawn_error=OSError("no process")),
        LifeEnd.SPAWN,
        ["arm 7.0", "spawn"],
    ),
    "a gate mismatch": (
        lambda: ScriptedLife(gate_admits=False),
        LifeEnd.GATE,
        ["arm 7.0", "spawn", "status", "kill", "wait", "close"],
    ),
    "an unreadable status": (
        lambda: ScriptedLife(status=OSError("gone")),
        LifeEnd.GATE,
        ["arm 7.0", "spawn", "status", "kill", "wait", "close"],
    ),
    "a broken pipe at the release": (
        lambda: ScriptedLife(release_error=BrokenPipeError()),
        LifeEnd.GATE,
        ["arm 7.0", "spawn", "status", "release", "kill", "wait", "close"],
    ),
    "an exit status outside the kept set": (
        lambda: ScriptedLife(exit_status=1),
        LifeEnd.WIRE_EXIT,
        ["arm 7.0", "spawn", "status", "release", "wait-within", "close"],
    ),
    "a child killed by a signal": (
        lambda: ScriptedLife(exit_status=-9),
        LifeEnd.WIRE_EXIT,
        ["arm 7.0", "spawn", "status", "release", "wait-within", "close"],
    ),
    "an accepted step": (
        lambda: ScriptedLife(exit_status=WireExit.ACCEPTED),
        LifeEnd.CONSUMED,
        [
            "arm 7.0",
            *IDLE_LEG,
            "arm 447.0",
            "seal",
            "open-deck",
            *CHILD_LEG,
            "close-deck",
            *STEP_LEG,
            "sweep 105.0",
            *SENDER_LEG,
        ],
    ),
    "a sweep that is not quiet": (
        lambda: ScriptedLife(sweep_quiet=False),
        LifeEnd.SWEEP,
        [
            "arm 7.0",
            "spawn",
            "status",
            "release",
            "wait-within",
            "close",
            "sweep 104.0",
        ],
    ),
}


@pytest.mark.parametrize(
    ("make_life", "life_end", "events"), LIFE_ENDS.values(), ids=LIFE_ENDS.keys()
)
def test_a_failing_step_ends_the_life_and_leaves_the_watchdog_armed(
    volume: Path,
    make_life: Callable[[], ScriptedLife],
    life_end: LifeEnd,
    events: list[str],
) -> None:
    life = make_life()
    with listening_with(volume, accepts=1) as listener:
        what_the_client_read = reading_after_a_proven_hello(volume)
        outcome = accept_from(life, listener)
        assert what_the_client_read() == b""

    assert outcome == life_end
    assert life.events == events


@pytest.mark.parametrize(
    ("make_life", "life_end", "_events"), LIFE_ENDS.values(), ids=LIFE_ENDS.keys()
)
def test_serve_returns_zero_and_writes_the_one_connection_line_of_what_ended_the_life(
    volume: Path,
    capsys: pytest.CaptureFixture[str],
    make_life: Callable[[], ScriptedLife],
    life_end: LifeEnd,
    _events: list[str],
) -> None:
    life = make_life()
    outcome: list[int] = []
    with json_logged(capsys) as logged:
        thread = threading.Thread(
            target=lambda: outcome.append(
                worker.serve(
                    port=int((volume / "port").read_text()),
                    key=(volume / "key").read_bytes(),
                    serving=life.serving(),
                ),
            ),
            daemon=True,
        )
        thread.start()
        try:
            connected_when_bound(volume, thread.is_alive).close()
            thread.join(WAIT_SECONDS)
        finally:
            life.spawn_error = OSError("stop")
            wake_until_ended(volume, thread)

    assert outcome == [0]
    (ended,) = events_of(logged, "connection.ended")
    expected = (
        ("info", "ok", None)
        if life_end is LifeEnd.CONSUMED
        else ("error", "failed", life_end)
    )
    actual = (ended["level"], ended["outcome"], ended.get("reason"))
    assert actual == expected
    assert [
        line["event"] for line in logged.lines if "process" in str(line["event"])
    ] == [
        "process.ready",
        "process.stopped",
    ]


@pytest.mark.parametrize(
    ("make_life", "fields"),
    [
        (
            lambda: ScriptedLife(exit_status=1),
            {"level": "info", "outcome": "ok", "exit_code": 1},
        ),
        (
            lambda: ScriptedLife(spawn_error=OSError("no process")),
            {"level": "warning", "outcome": "failed", "reason": LifeEnd.SPAWN},
        ),
    ],
    ids=["a child that exited", "a spawn that failed"],
)
def test_a_phase_writes_one_timed_line_with_its_role_and_how_it_ended(
    volume: Path,
    capsys: pytest.CaptureFixture[str],
    make_life: Callable[[], ScriptedLife],
    fields: dict[str, object],
) -> None:
    with listening_with(volume, accepts=1) as listener:
        reading = reading_after_a_proven_hello(volume)
        with json_logged(capsys) as logged:
            accept_from(make_life(), listener)
        reading()

    (line,) = events_of(logged, "wire_phase.ended")
    assert line["phase"] == "idle"
    assert {key: line[key] for key in fields} == fields


def test_a_sweep_that_fails_for_another_reason_surfaces_with_its_error(
    volume: Path,
) -> None:
    life = ScriptedLife(sweep_error=PermissionError("kill refused"))
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        with pytest.raises(PermissionError, match="kill refused"):
            accept_from(life, listener)

    assert "cancel" not in life.events


class FailingAcceptListener(BoundedListener):
    """A listener whose accept fails as an exhausted descriptor table does."""

    def accept(self) -> NoReturn:
        raise OSError(errno.EMFILE, "too many open files")


def test_a_failing_accept_ends_the_life_before_anything_is_armed_spawned_or_swept(
    volume: Path,
) -> None:
    life = ScriptedLife()
    with listening_with(volume, 0, FailingAcceptListener) as listener:
        outcome = accept_from(life, listener)

    assert outcome == LifeEnd.ACCEPT
    assert life.events == []


def test_serve_returns_zero_and_writes_one_error_line_for_the_accept_fault(
    volume: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bind_listener = worker.bind_listener

    def bind_failing_accept(port: int) -> FailingAcceptListener:
        plain = bind_listener(port)
        return FailingAcceptListener(fileno=plain.detach())

    monkeypatch.setattr(worker, "bind_listener", bind_failing_accept)

    with json_logged(capsys) as logged:
        status = worker.serve(
            port=int((volume / "port").read_text()),
            key=(volume / "key").read_bytes(),
            serving=ScriptedLife().serving(),
        )

    assert status == 0
    (fault,) = events_of(logged, "connection.failed")
    assert (fault["level"], fault["error_type"]) == ("error", "OSError")
    assert "too many open files" not in logged.text
    assert "Traceback" not in logged.text
    assert events_of(logged, "connection.ended") == []


def test_the_runtime_uid_gets_one_gated_child_per_connection(volume: Path) -> None:
    life = ScriptedLife()
    with listening_with(volume, accepts=CONNECTIONS) as listener:
        proven(volume)
        proven(volume)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    assert len(life.spawned) == CONNECTIONS


def test_serve_binds_listens_and_hands_over_connections_until_the_life_ends(
    volume: Path,
) -> None:
    with serving(volume, admitted=CONNECTIONS) as (thread, handled, outcome):
        for _ in range(CONNECTIONS):
            client = connected_when_bound(volume, thread.is_alive)
            assert client.recv(1) == b""
            client.close()
        thread.join(WAIT_SECONDS)

        assert not thread.is_alive()
        assert len(handled) == CONNECTIONS
        assert outcome == [0]


def test_a_failure_inside_serving_still_ends_the_serving_thread(volume: Path) -> None:
    threads: list[threading.Thread] = []

    def fail_before_any_connection() -> None:
        with serving(volume, admitted=CONNECTIONS) as (thread, _, _):
            threads.append(thread)
            raise StopServingError

    with pytest.raises(StopServingError):
        fail_before_any_connection()

    assert not threads[0].is_alive()


def test_an_unexpected_serving_error_surfaces_to_the_caller(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = RuntimeError("serving broke")

    def broken(*_arguments: object, **_keywords: object) -> NoReturn:
        raise failure

    monkeypatch.setattr(worker, "serve", broken)

    with (
        pytest.raises(RuntimeError) as surfaced,
        serving(volume, admitted=CONNECTIONS),
    ):
        pass

    assert surfaced.value is failure


def test_a_body_failure_carries_the_serving_error_as_a_note(
    volume: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serving_failure = RuntimeError("serving broke")
    body_failure = AssertionError("body broke")

    def broken(*_arguments: object, **_keywords: object) -> NoReturn:
        raise serving_failure

    monkeypatch.setattr(worker, "serve", broken)

    with (
        pytest.raises(AssertionError) as surfaced,
        serving(volume, admitted=CONNECTIONS),
    ):
        raise body_failure

    assert surfaced.value is body_failure
    assert any("serving broke" in note for note in body_failure.__notes__)


def accept_twice_from(life: ScriptedLife, volume: Path) -> list[bytes]:
    with listening_with(volume, accepts=2) as listener:
        reads = [
            reading_after_a_proven_hello(volume),
            reading_after_a_proven_hello(volume),
        ]
        with pytest.raises(StopServingError):
            accept_from(life, listener)
        return [read() for read in reads]


def test_a_header_that_outlasts_its_budget_is_cut_off_and_the_next_connection_is_served(
    volume: Path,
) -> None:
    life = ScriptedLife(headers_stalled=1)

    replies = accept_twice_from(life, volume)

    assert replies == [b"", b""]
    assert life.budgets_waited == [HEADER_SECONDS, HEADER_SECONDS]
    assert life.events == CUT_OFF_HEADER + ANSWERED_HEADER


def test_a_stalled_header_never_ends_the_life_while_the_sweep_is_quiet(
    volume: Path,
) -> None:
    life = ScriptedLife(headers_stalled=2)
    with listening_with(volume, accepts=2) as listener:
        proven(volume)
        proven(volume)
        with pytest.raises(StopServingError):
            accept_from(life, listener)

    assert life.events == 2 * CUT_OFF_HEADER


def test_serve_cuts_a_stalled_header_off_and_serves_the_next_connection_in_one_life(
    volume: Path,
) -> None:
    life = ScriptedLife(
        headers_stalled=1,
        connections_served_before_sweep_fails=2,
        answer=STATUS,
    )
    outcome: list[int] = []
    thread = threading.Thread(
        target=lambda: outcome.append(
            worker.serve(
                port=int((volume / "port").read_text()),
                key=(volume / "key").read_bytes(),
                serving=life.serving(),
            )
        ),
        daemon=True,
    )
    thread.start()
    try:
        first = connected_when_bound(volume, thread.is_alive)
        assert first.recv(1) == b""
        second = connected_when_bound(volume, thread.is_alive)
        second.sendall(VALID_STEP)
        assert second.recv(len(STATUS)) == STATUS
        thread.join(WAIT_SECONDS)
    finally:
        life.spawn_error = OSError("stop")
        wake_until_ended(volume, thread)

    assert outcome == [0]
    assert len(life.spawned) == CONNECTIONS
    assert life.events == CUT_OFF_HEADER + ANSWERED_HEADER[:-1]


def test_a_gate_mismatch_is_a_gate_end_whatever_the_clock_says(volume: Path) -> None:
    life = ScriptedLife(gate_admits=False, inspection_takes=HEADER_SECONDS)
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        outcome = accept_from(life, listener)

    assert outcome == LifeEnd.GATE
    assert "release" not in life.events


def test_the_locale_key_python_sets_itself_passes_the_environment_predicate() -> None:
    """A5 (#431): the locale key Python sets itself is admitted."""
    snapshot = with_fields(environment_keys=frozenset({"PATH", "LC_CTYPE"}))(
        sealed_snapshot()
    )

    assert worker_self_check.first_failing_predicate(snapshot) is None
