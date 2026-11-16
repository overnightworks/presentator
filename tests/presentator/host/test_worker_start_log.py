"""The build worker's start writes JSON lines under its own service name."""

import os
import subprocess
import sys
from pathlib import Path

from presentator.host import worker
from tests.support.log_lines import json_lines

_CONSOLE_ENTRY_POINT = Path(sys.executable).parent / "presentator-worker"


def test_a_refused_start_writes_one_json_line_of_the_workers_service() -> None:
    started = subprocess.run(  # noqa: S603 - the console script of this environment
        [str(_CONSOLE_ENTRY_POINT), "--check"],
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )

    (refusal,) = json_lines(started.stdout)
    assert started.returncode == worker.REFUSAL_EXIT_STATUS
    assert refusal["service"] == worker.SERVICE_NAME
    assert refusal["level"] == "error"
