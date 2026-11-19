"""A stop signal ends the build worker in its one stop line, wherever it lands."""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from presentator.host.lifecycle import ServerEvent
from tests.presentator.application.support.fakes import PATIENCE
from tests.presentator.host.support.worker_process import Phase
from tests.support.log_lines import json_lines

_CHILD = "tests.presentator.host.support.worker_process"
# A module started with `-m` imports `tests` from here.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _read_until(process: subprocess.Popen[str], event: str) -> list[str]:
    assert process.stdout is not None
    read: list[str] = []
    for line in process.stdout:
        read.append(line)
        if json.loads(line)["event"] == event:
            return read
    pytest.fail(f"the worker ended before {event}")


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize(
    ("phase", "reached"),
    [
        pytest.param(Phase.START_UP, "process.started", id="in its self-check"),
        pytest.param(Phase.SERVING, "process.ready", id="while it serves"),
    ],
)
def test_a_stop_signal_ends_the_worker_in_its_stop_line_alone(
    phase: Phase, reached: str, stop_signal: signal.Signals
) -> None:
    with subprocess.Popen(  # noqa: S603 - the command is this repository's own
        [sys.executable, "-m", _CHILD, phase],
        env={"PATH": os.environ["PATH"], "PYTHONPATH": str(_REPOSITORY_ROOT)},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            before = _read_until(process, reached)
            process.send_signal(stop_signal)
            after, errors = process.communicate(timeout=PATIENCE.total_seconds())
        finally:
            process.kill()

    events = [line["event"] for line in json_lines("".join(before) + after)]
    # The signal's default action, which a shell or a container runtime reports
    # as 128 plus the signal's number.
    assert process.returncode == -stop_signal
    assert events[-1] == ServerEvent.PROCESS_STOPPED
    assert events.count(ServerEvent.PROCESS_STOPPED) == 1
    assert "process.crashed" not in events
    assert errors == ""
