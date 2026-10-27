"""The request record through the real idle and upload roles as child processes."""

import errno
import json
import os
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from presentator.adapters.worker_children import (
    spawn_gated,
)
from presentator.host import worker, worker_serving
from presentator.host.worker_serving import (
    LifeEnd,
)
from tests.presentator.host.support.worker_life import (
    EXITING_SENT,
    PATH,
    LifeScenario,
    end_controller,
    logged_process_ids,
    run_life,
)
from tests.support.step_support import OWN_IDENTITY
from tests.support.worker_chain import (
    SLEEPER,
    WAIT_SECONDS,
    ChainSpawn,
)
from tests.support.worker_support import (
    END,
    STATUS,
    STEP_HEADER,
    VALID_STEP,
    RecordingCreation,
    connect_when_serving,
)

FORGER = """
import os, sys
os.pwrite(int(sys.argv[2]), bytes.fromhex(sys.argv[3]), 0)
sys.exit(5)
"""
INODES_REPORTER = """
import json, os, sys
identities = []
for name in os.listdir("/proc/self/fd"):
    try:
        held = os.stat("/proc/self/fd/" + name)
    except OSError:
        continue
    identities.append([held.st_dev, held.st_ino])
os.write(int(sys.argv[1]), json.dumps(identities).encode())
sys.exit(4)
"""


@pytest.fixture
def path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", PATH)


@pytest.mark.usefixtures("path")
def test_the_upload_child_answers_status_from_the_record_the_idle_child_wrote() -> None:
    written: list[bytes] = []

    def seal_then_read_as_the_test(record: int) -> None:
        worker.seal_record(record)
        written.append(os.pread(record, 4096, 0))

    life = run_life(
        VALID_STEP + END,
        ChainSpawn(programs={"send": [EXITING_SENT]}),
        LifeScenario(seal=seal_then_read_as_the_test),
    )

    assert life.reply == STATUS
    assert life.life_end == LifeEnd.CONSUMED
    assert life.spawn.roles == ["idle", "upload", "step", "send"]
    assert written == [STEP_HEADER]


FORGED_RECORDS = {
    "empty": b"",
    "truncated": STEP_HEADER[:-4],
    "noise": b"\x00\xff\x10 noise",
    "an unknown step": b'{"step":"deploy","slug":"my-deck"}',
    "slug ..": b'{"step":"build","slug":".."}',
    "a slug with a slash": b'{"step":"build","slug":"a/b"}',
}


@pytest.mark.usefixtures("path")
@pytest.mark.parametrize("forged", FORGED_RECORDS.values(), ids=FORGED_RECORDS.keys())
def test_an_undecodable_record_closes_the_upload_child_before_status_and_ends_the_life(
    forged: bytes,
) -> None:
    spawn = ChainSpawn(programs={"idle": [FORGER, forged.hex()]})
    life = run_life(b"", spawn)

    assert life.reply == b""
    assert life.life_end == LifeEnd.WIRE_EXIT
    assert spawn.roles == ["idle", "upload"]


@pytest.mark.usefixtures("path")
def test_a_failing_seal_closes_without_status_and_spawns_no_upload_child() -> None:
    def refuse(_record: int) -> None:
        raise OSError(errno.EBUSY, "a writable shared mapping exists")

    life = run_life(VALID_STEP, ChainSpawn(), LifeScenario(seal=refuse))

    assert life.reply == b""
    assert life.life_end == LifeEnd.SEAL_FAILED
    assert life.spawn.roles == ["idle"]


@pytest.mark.usefixtures("path")
def test_a_stalled_upload_child_is_killed_at_the_end_of_its_budget_and_reaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_serving, "UPLOAD_SECONDS", 1.0)
    spawn = ChainSpawn(programs={"upload": [SLEEPER]})

    started = time.monotonic()
    life = run_life(VALID_STEP, spawn)

    assert life.reply == b""
    assert life.life_end == LifeEnd.UPLOAD_EXPIRED
    assert 1.0 <= time.monotonic() - started < 1.0 + WAIT_SECONDS / 2
    assert spawn.roles == ["idle", "upload"]


@pytest.mark.usefixtures("path")
def test_the_next_life_does_not_hold_the_record_of_the_one_before() -> None:
    creation = RecordingCreation()
    records: list[tuple[int, int]] = []
    held_by_child: list[set[tuple[int, int]]] = []
    for _life in range(2):
        life = run_life(
            b"",
            ChainSpawn(programs={"idle": [INODES_REPORTER]}),
            LifeScenario(create_record=creation),
        )
        records.append(creation.identity)
        held_by_child.append({tuple(held) for held in json.loads(life.reply)})
        assert creation.is_closed()

    assert records[0] != records[1]
    assert records[0] in held_by_child[0]
    assert records[1] in held_by_child[1]
    assert records[0] not in held_by_child[1]


STALLED_AFTER_UPLOAD_SPAWN = """
import ctypes, os, sys, time
from functools import partial
from pathlib import Path
from presentator.adapters.worker_children import (
    ChildIdentity,
    readable_within,
    spawn_gated,
)
from presentator.host import worker
from presentator.host.worker_serving import Serving, StepEffects
from presentator.host.worker_termination import FaultHandlerWatchdog

log = open(sys.argv[2], "a")
real_spawn = partial(
    spawn_gated, identity=ChildIdentity(os.getuid(), os.getgid(), extra_groups=None)
)

def spawn(command, *, environment, pass_fds):
    child = real_spawn(command, environment=environment, pass_fds=pass_fds)
    if "upload" in command:
        pid = child.status_text().split("Pid:")[1].split()[0]
        print("pid", pid, file=log, flush=True)
        ctypes.PyDLL(None).pause()
    return child

def refuse_step(*_arguments, **_keywords):
    raise AssertionError("the step is not part of this test")

def clock_announcing_each_reading():
    reading = time.monotonic()
    print(reading, file=log, flush=True)
    return reading

serving = Serving(
    spawn=spawn,
    gate_admits=lambda _status: True,
    sweep=lambda _deadline: True,
    create_record=worker.create_request_record,
    seal=worker.seal_record,
    open_deck=lambda _deck: None,
    close_deck=lambda _deck: None,
    work=Path("/unused/work"),
    step=StepEffects(
        spawn=refuse_step,
        gate_admits=lambda _status: False,
        prepare=refuse_step,
        environment={},
        project=Path("/unused/project"),
    ),
    watchdog=FaultHandlerWatchdog(),
    clock=clock_announcing_each_reading,
    readable=readable_within,
    build_timeout_seconds=1.0,
    build_output_megabytes=300,
)
sys.exit(
    worker.serve(
        Path(sys.argv[1]),
        controller_uid=os.getuid(),
        controller_gid=os.getgid(),
        runtime_uid=os.getuid(),
        serving=serving,
    )
)
"""
EMERGENCY_END_SECONDS = 148.0
TOLERANCE_SECONDS = 1.0


def test_a_controller_that_stalls_after_the_upload_spawn_dies_at_the_emergency_end(
    volume: Path, tmp_path: Path
) -> None:
    log = tmp_path / "controller.log"
    log.touch()
    controller = spawn_gated(
        [sys.executable, "-I", "-c", STALLED_AFTER_UPLOAD_SPAWN, str(volume), str(log)],
        identity=OWN_IDENTITY,
        environment={"PATH": PATH},
        pass_fds=(),
    )
    try:
        controller.release()
        with closing(connect_when_serving(volume, controller, WAIT_SECONDS)) as client:
            client.sendall(VALID_STEP)
            exit_status = controller.wait_within(EMERGENCY_END_SECONDS + 30)
            exited_at = time.monotonic()
    finally:
        lines = end_controller(controller, log)
    upload_process_ids = logged_process_ids(lines)
    accepted_at = float(lines[0])

    assert len(upload_process_ids) == 1
    assert exit_status == 1
    assert abs(exited_at - accepted_at - EMERGENCY_END_SECONDS) <= TOLERANCE_SECONDS
