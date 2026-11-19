"""A SIGINT anywhere in a console script's start writes only JSON lines, or nothing."""

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from presentator.host import worker
from presentator.host.lifecycle import ServerEvent
from tests.presentator.application.support.fakes import PATIENCE
from tests.presentator.host.support.held_start.sitecustomize import (
    AT_EXIT,
    HELD,
    HELD_DESCRIPTOR,
    HOLD_AT,
)
from tests.support.log_lines import json_lines
from tests.support.two_processes import CONSOLE_ENTRY_POINT, WORKER_CONSOLE_ENTRY_POINT

_HELD_START = Path(__file__).resolve().parent / "support" / "held_start"


@dataclass(frozen=True, slots=True)
class _Interrupted:
    exit_code: int
    output: str
    errors: str


def _interrupted_while_held(console_script: Path, held_at: str) -> _Interrupted:
    """Start the script with no settings, and SIGINT it once it holds at `held_at`."""
    read_end, write_end = os.pipe()
    with subprocess.Popen(  # noqa: S603 - the console script of this environment
        [str(console_script), "--check"]
        if console_script == WORKER_CONSOLE_ENTRY_POINT
        else [str(console_script)],
        env={
            "PATH": os.environ["PATH"],
            "PYTHONPATH": str(_HELD_START),
            HOLD_AT: held_at,
            HELD_DESCRIPTOR: str(write_end),
        },
        pass_fds=(write_end,),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        os.close(write_end)
        try:
            with os.fdopen(read_end, "rb") as held:
                assert held.read(len(HELD)) == HELD
            process.send_signal(signal.SIGINT)
            output, errors = process.communicate(timeout=PATIENCE.total_seconds())
        finally:
            process.kill()
    return _Interrupted(process.returncode, output, errors)


@pytest.mark.parametrize(
    ("console_script", "held_at"),
    [
        (CONSOLE_ENTRY_POINT, "presentator.host.main"),
        (CONSOLE_ENTRY_POINT, "logline"),
        (CONSOLE_ENTRY_POINT, "sqlalchemy"),
        (CONSOLE_ENTRY_POINT, "uvicorn"),
        (WORKER_CONSOLE_ENTRY_POINT, "presentator.host.worker"),
        (WORKER_CONSOLE_ENTRY_POINT, "logline"),
        (WORKER_CONSOLE_ENTRY_POINT, "presentator.host.worker_serving"),
    ],
)
def test_a_sigint_while_a_console_script_imports_ends_it_silently(
    console_script: Path, held_at: str
) -> None:
    interrupted = _interrupted_while_held(console_script, held_at)

    # The default action of SIGINT, which a shell or a container runtime reports as 130.
    assert interrupted.exit_code == -signal.SIGINT
    assert interrupted.output == ""
    assert interrupted.errors == ""


@pytest.mark.parametrize(
    ("console_script", "refusal"),
    [
        (CONSOLE_ENTRY_POINT, ServerEvent.PROCESS_FAILED),
        (WORKER_CONSOLE_ENTRY_POINT, worker.START_REFUSED_EVENT),
    ],
)
def test_a_sigint_after_a_refused_start_leaves_its_refusal_the_last_line(
    console_script: Path, refusal: str
) -> None:
    interrupted = _interrupted_while_held(console_script, AT_EXIT)

    assert interrupted.exit_code == -signal.SIGINT
    assert [line["event"] for line in json_lines(interrupted.output)][-1:] == [refusal]
    assert interrupted.errors == ""
