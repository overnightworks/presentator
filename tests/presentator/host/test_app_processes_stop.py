"""Stopping an app reads its output to the end or names the app that never ends."""

import os
import signal
import subprocess
import sys

import pytest

from tests.presentator.host.support.app_processes import App

_LEAVES_A_HOLDER_OF_THE_OUTPUT = (
    "import subprocess, sys;"
    "subprocess.Popen(['sleep', '60']);"
    'print(\'{"event": "last words"}\', flush=True);'
    "sys.stdin.readline()"
)


def test_a_held_open_output_fails_the_stop_naming_the_app_and_its_last_lines() -> None:
    with subprocess.Popen(  # noqa: S603 - the command is this test's own
        [sys.executable, "-c", _LEAVES_A_HOLDER_OF_THE_OUTPUT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    ) as process:
        app = App(process, 4711)
        try:
            app.log_line_where(lambda _: True)
            with pytest.raises(AssertionError, match=r"(?s)port 4711.*last words"):
                app.stop(within=1)
        finally:
            os.killpg(process.pid, signal.SIGKILL)
        assert app.ended.wait(timeout=30)
