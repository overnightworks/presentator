"""The build worker's start writes JSON lines under its own service name."""

import os
import subprocess

from presentator.host import worker
from tests.support.log_lines import json_lines
from tests.support.two_processes import WORKER_CONSOLE_ENTRY_POINT


def test_a_refused_start_writes_one_json_line_of_the_workers_service() -> None:
    started = subprocess.run(  # noqa: S603 - the console script of this environment
        [str(WORKER_CONSOLE_ENTRY_POINT), "--check"],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    (refusal,) = json_lines(started.stdout)
    assert started.returncode == worker.REFUSAL_EXIT_STATUS
    assert refusal["service"] == worker.SERVICE_NAME
    assert refusal["level"] == "error"
