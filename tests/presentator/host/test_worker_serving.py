"""The build worker reads its key, binds its port and says when it is ready."""

import errno
import socket
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.host import worker
from presentator.host.lifecycle import StopLine
from presentator.host.worker import StartRefusal
from presentator.host.worker_serving import Serving
from presentator.host.worker_settings import MINIMUM_KEY_LENGTH
from tests.presentator.host.support.worker_boundary import LOADED_SETTINGS
from tests.presentator.host.support.worker_lines import refusal_reasons
from tests.support.log_lines import json_logged
from tests.support.worker_support import KEY, OWN_UID, ScriptedLife, free_port

PORT = 7100
USAGE_EXIT_STATUS = 2
TABLE_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue\n"


def table_row(port: int, state: str) -> str:
    return f"   0: 00000000:{port:04X} 00000000:0000 {state} 00000000:00000000\n"


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "key"
    path.write_bytes(KEY + b"\n")
    path.chmod(0o400)
    return path


def test_the_key_is_the_file_minus_its_one_newline(key_file: Path) -> None:
    assert worker.read_key(key_file, controller_uid=OWN_UID) == KEY


def test_the_key_is_read_through_a_link_as_a_mounted_secret_is(
    key_file: Path, tmp_path: Path
) -> None:
    link = tmp_path / "mounted"
    link.symlink_to(key_file)

    assert worker.read_key(link, controller_uid=OWN_UID) == KEY


def a_key_file_of_another_owner(_path: Path) -> int:
    return OWN_UID + 1


def a_key_file_others_can_read(path: Path) -> int:
    path.chmod(0o440)
    return OWN_UID


def a_key_file_that_is_a_directory(path: Path) -> int:
    path.unlink()
    path.mkdir()
    return OWN_UID


def a_key_file_that_is_too_short(path: Path) -> int:
    path.chmod(0o600)
    path.write_bytes(b"k" * (MINIMUM_KEY_LENGTH - 1))
    path.chmod(0o400)
    return OWN_UID


def a_missing_key_file(path: Path) -> int:
    path.unlink()
    return OWN_UID


KEY_FAULTS = {
    "another owner": a_key_file_of_another_owner,
    "readable by others": a_key_file_others_can_read,
    "a directory": a_key_file_that_is_a_directory,
    "too short": a_key_file_that_is_too_short,
    "missing": a_missing_key_file,
}


@pytest.mark.parametrize("fault", KEY_FAULTS.values(), ids=KEY_FAULTS.keys())
def test_a_key_file_that_is_not_the_controllers_secret_refuses(
    key_file: Path, fault: Callable[[Path], int]
) -> None:
    controller_uid = fault(key_file)

    with pytest.raises(worker.StartRefusedError) as refused:
        worker.read_key(key_file, controller_uid=controller_uid)

    assert refused.value.refusal == StartRefusal.KEY


def test_the_listener_accepts_a_connection_once_it_listens() -> None:
    port = free_port()
    with worker.bind_listener(port) as listener:
        worker.start_listening(listener)
        socket.create_connection(("127.0.0.1", port)).close()


def test_a_port_that_is_taken_refuses_as_bind() -> None:
    with worker.bind_listener(0) as holder:
        worker.start_listening(holder)
        taken = holder.getsockname()[1]
        with pytest.raises(worker.StartRefusedError) as refused:
            worker.bind_listener(taken)

    assert refused.value.refusal == StartRefusal.BIND


def test_no_socket_can_be_allocated_refuses_the_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def exhausted(*_arguments: object) -> socket.socket:
        raise OSError(errno.EMFILE, "too many open files")

    monkeypatch.setattr(worker.socket, "socket", exhausted)

    with json_logged(capsys) as logged:
        status = worker.serve(
            port=PORT,
            key=KEY,
            serving=ScriptedLife().serving(),
            stop_line=StopLine(),
        )

    assert status == worker.REFUSAL_EXIT_STATUS
    assert refusal_reasons(logged) == [StartRefusal.BIND]


def test_listen_failure_is_a_named_refusal() -> None:
    unbound = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    with unbound, pytest.raises(worker.StartRefusedError) as refused:
        worker.start_listening(unbound)

    assert refused.value.refusal == StartRefusal.LISTEN


@pytest.mark.parametrize(
    ("table", "ready"),
    [
        (TABLE_HEADER + table_row(PORT, worker.LISTENING), True),
        (TABLE_HEADER, False),
        (TABLE_HEADER + table_row(PORT + 1, worker.LISTENING), False),
        (
            TABLE_HEADER
            + table_row(PORT, worker.LISTENING)
            + table_row(PORT, worker.ESTABLISHED),
            False,
        ),
        (
            TABLE_HEADER
            + table_row(PORT, worker.LISTENING)
            + table_row(PORT + 1, worker.ESTABLISHED)
            + table_row(PORT, "06"),
            True,
        ),
    ],
    ids=["listening", "empty", "another port", "serving a life", "other sockets"],
)
def test_the_worker_is_ready_when_it_listens_and_holds_no_connection(
    table: str, *, ready: bool
) -> None:
    assert worker.is_ready(PORT, table) is ready


def raising(error: Exception) -> Callable[[], str]:
    def read() -> str:
        raise error

    return read


@pytest.mark.parametrize(
    ("read_table", "status"),
    [
        (lambda: TABLE_HEADER + table_row(PORT, worker.LISTENING), 0),
        (lambda: TABLE_HEADER, worker.NOT_READY_EXIT_STATUS),
        (raising(OSError()), worker.NOT_READY_EXIT_STATUS),
        (lambda: TABLE_HEADER + "garbled\n", worker.NOT_READY_EXIT_STATUS),
    ],
    ids=["listening", "not listening", "table unreadable", "table garbled"],
)
def test_the_readiness_probe_exits_zero_only_for_a_worker_that_listens(
    read_table: Callable[[], str], status: int
) -> None:
    boundary = replace(worker.REAL_BOUNDARY, read_socket_table=read_table)

    assert worker.main(["--ready", "--port", str(PORT)], boundary) == status


@pytest.mark.parametrize(
    "arguments",
    [
        ["--ready"],
        ["--key-file", "/run/key"],
        ["--port", str(PORT)],
        [],
    ],
    ids=["ready without port", "serve without port", "serve without key", "bare"],
)
def test_a_start_names_the_port_and_key_file_it_serves_or_is_refused_as_usage(
    arguments: list[str],
) -> None:
    with pytest.raises(SystemExit) as refused:
        worker.main(arguments)

    assert refused.value.code == USAGE_EXIT_STATUS


def test_a_key_the_real_boundary_refuses_ends_the_start_before_binding(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with json_logged(capsys) as logged:
        status = worker.REAL_BOUNDARY.serve(
            port=PORT,
            key_file=tmp_path / "missing",
            settings=LOADED_SETTINGS,
            stop_line=StopLine(),
        )

    assert status == worker.REFUSAL_EXIT_STATUS
    assert refusal_reasons(logged) == [StartRefusal.KEY]


def test_serving_on_this_machine_serves_the_port_with_the_key_bounded_by_the_settings(
    tmp_path: Path,
) -> None:
    key_file = tmp_path / "key"
    served: list[tuple[int, bytes, Serving]] = []

    def recording_serve(
        *, port: int, key: bytes, serving: Serving, stop_line: StopLine
    ) -> int:
        del stop_line
        served.append((port, key, serving))
        return 0

    keys = {key_file: KEY}
    serving_on_this_machine = worker.ServingOnThisMachine(
        keys.__getitem__, recording_serve
    )

    status = serving_on_this_machine(
        port=PORT, key_file=key_file, settings=LOADED_SETTINGS, stop_line=StopLine()
    )

    [(port, key, serving)] = served
    assert (status, port, key) == (0, PORT, KEY)
    assert (serving.build_timeout_seconds, serving.build_output_megabytes) == (
        LOADED_SETTINGS.build_timeout_seconds,
        LOADED_SETTINGS.build_output_megabytes,
    )
