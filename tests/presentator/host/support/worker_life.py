"""One controller life over the real gated spawn, with its client and cleanup."""

import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack, closing, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from tempfile import TemporaryDirectory

from presentator.adapters.worker_children import GatedChild, readable_within
from presentator.host import worker
from presentator.host.worker_serving import LifeEnd, Serving, StepEffects, hand_over
from presentator.host.worker_wire import WireExit
from tests.support.worker_chain import WAIT_SECONDS, ChainSpawn, await_absence
from tests.support.worker_support import NeverArmed, listening_socket, present_among

PATH = "/usr/bin:/bin"
EXITING_SENT = f"import sys; sys.exit({int(WireExit.SENT)})"


@dataclass
class Life:
    """What one controller life did, as the client and the controller saw it."""

    life_end: LifeEnd | None
    reply: bytes
    spawn: ChainSpawn
    started_at: float
    returned_at: float
    present_at_return: frozenset[int]


def read_to_end(client: socket.socket) -> bytes:
    """Read until a clean EOF; a reset raises."""
    received = b""
    while chunk := client.recv(4096):
        received += chunk
    return received


def read_to_end_or_reset(client: socket.socket) -> bytes:
    """Read until EOF or a reset, for refusals that leave bytes unread."""
    received = b""
    with suppress(ConnectionResetError):
        while chunk := client.recv(4096):
            received += chunk
    return received


@dataclass(frozen=True)
class LifeScenario:
    """What a life runs on besides the spawn: record effects, places and reader."""

    seal: Callable[[int], None] = worker.seal_record
    create_record: Callable[[], int] = worker.create_request_record
    work: Path | None = None
    project: Path | None = None
    build_timeout_seconds: float = 300.0
    reader: Callable[[socket.socket], bytes] = read_to_end
    listener: socket.socket | None = None


def chain_environment(work: Path) -> dict[str, str]:
    """The six-key step environment, with test values below the work root."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(work / worker.HOME_DIRECTORY),
        "TMPDIR": str(work),
        "PLAYWRIGHT_BROWSERS_PATH": str(work / "playwright"),
        "COREPACK_HOME": str(work / "corepack"),
        "COREPACK_ENABLE_NETWORK": "0",
    }


def run_life(
    sent: bytes,
    spawn: ChainSpawn,
    scenario: LifeScenario | None = None,
    converse: Callable[[socket.socket], None] = lambda _client: None,
) -> Life:
    scenario = scenario or LifeScenario()
    with ExitStack() as cleanup:
        work = scenario.work or Path(cleanup.enter_context(TemporaryDirectory()))
        project = scenario.project or Path(cleanup.enter_context(TemporaryDirectory()))
        listener = scenario.listener or cleanup.enter_context(listening_socket())
        return _run_life_at(
            sent,
            spawn,
            replace(scenario, work=work, project=project, listener=listener),
            converse,
        )


def _end_life(
    spawn: ChainSpawn, thread: threading.Thread, client: socket.socket
) -> None:
    try:
        client.close()
    finally:
        try:
            spawn.kill_and_reap()
        finally:
            thread.join(WAIT_SECONDS)
            assert not thread.is_alive(), "the controller outlived its life"


def _run_life_at(
    sent: bytes,
    spawn: ChainSpawn,
    scenario: LifeScenario,
    converse: Callable[[socket.socket], None],
) -> Life:
    work, project, listener = scenario.work, scenario.project, scenario.listener
    assert work is not None
    assert project is not None
    assert listener is not None
    controller_end, client = socket.socketpair()
    client.settimeout(WAIT_SECONDS)
    serving = Serving(
        spawn=spawn,
        gate_admits=lambda _status: True,
        sweep=lambda _deadline: True,
        create_record=scenario.create_record,
        seal=scenario.seal,
        open_deck=worker.make_open_directory,
        close_deck=worker.close_deck,
        work=work,
        step=StepEffects(
            spawn=spawn.step,
            gate_admits=lambda _status: True,
            prepare=worker.prepare_step_paths,
            environment=chain_environment(work),
            project=project,
        ),
        watchdog=NeverArmed(),
        clock=time.monotonic,
        readable=readable_within,
        build_timeout_seconds=scenario.build_timeout_seconds,
        build_output_megabytes=300,
    )
    outcome: list[LifeEnd | None] = []
    returned_at: list[float] = []

    def control() -> None:
        outcome.append(
            hand_over(controller_end, serving, time.monotonic(), listener=listener)
        )
        returned_at.append(time.monotonic())

    thread = threading.Thread(target=control, daemon=True)
    with closing(controller_end), closing(client):
        try:
            started_at = time.monotonic()
            thread.start()
            client.sendall(sent)
            converse(client)
            thread.join(WAIT_SECONDS)
            present = present_among(spawn.process_ids)
            controller_end.close()
            reply = scenario.reader(client)
        finally:
            _end_life(spawn, thread, client)
        return Life(outcome[0], reply, spawn, started_at, returned_at[0], present)


def logged_process_ids(lines: list[str]) -> list[int]:
    return [int(line.split()[-1]) for line in lines if line.startswith("pid ")]


def end_controller(controller: GatedChild, log: Path) -> list[str]:
    """Stop the controller and SIGKILL every wire child it logged, even on a stall."""
    try:
        controller.close()
        controller.kill()
        assert controller.wait_within(WAIT_SECONDS) is not None, "controller not reaped"
    finally:
        lines = log.read_text().splitlines()
        process_ids = logged_process_ids(lines)
        for process_id in process_ids:
            with suppress(ProcessLookupError):
                os.kill(process_id, signal.SIGKILL)
        await_absence(process_ids)
    return lines
