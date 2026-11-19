"""The build worker's start as its own process, held in a start-up step or serving.

Run with `python -m <module> <phase>`, where the phase is `start-up` or
`serving`. A start-up process holds in its self-check until its standard input
ends; a serving one accepts on a free port, which this machine's sealed start
would refuse to reach. Either way only a signal ends it.
"""

import sys
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from presentator.host import worker
from presentator.host.lifecycle import StopLine
from presentator.host.worker_self_check import Snapshot
from presentator.host.worker_settings import WorkerSettings
from tests.presentator.host.support.sealed_snapshot import sealed_snapshot
from tests.presentator.host.support.worker_boundary import boundary_over
from tests.support.worker_support import ScriptedLife

_KEY = b"k" * 32
# Any port the kernel hands out: the parent reads the ready line, not the port.
_ARGUMENTS = ["--port", "0", "--key-file", "unread"]


class Phase(StrEnum):
    """Where the worker is when the parent signals it."""

    START_UP = "start-up"
    SERVING = "serving"


def _held_in_the_self_check() -> Snapshot:
    sys.stdin.readline()
    return sealed_snapshot()


def _serve_a_free_port(
    *, port: int, key_file: Path, settings: WorkerSettings, stop_line: StopLine
) -> int:
    del key_file, settings
    return worker.serve(
        port=port, key=_KEY, serving=ScriptedLife().serving(), stop_line=stop_line
    )


def boundary_for(phase: Phase) -> worker.Boundary:
    """The fake boundary of `phase`, whose steps never end on their own."""
    boundary = boundary_over(sealed_snapshot(), [])
    if phase is Phase.START_UP:
        return replace(boundary, read_snapshot=_held_in_the_self_check)
    return replace(boundary, serve=_serve_a_free_port)


if __name__ == "__main__":
    sys.exit(worker.main(_ARGUMENTS, boundary_for(Phase(sys.argv[1]))))
