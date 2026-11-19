"""A console script refuses a wrong or missing argument in one coded JSON line."""

import subprocess
from pathlib import Path

import pytest

from presentator.host.lifecycle import (
    ARGUMENT_REFUSED_EXIT_STATUS,
    ServerEvent,
    ServerReason,
)
from tests.presentator.application.support.fakes import PATIENCE
from tests.support.log_lines import json_lines
from tests.support.two_processes import (
    CONSOLE_ENTRY_POINT,
    WORKER_CONSOLE_ENTRY_POINT,
    measured,
)

# A word that stands where a secret could: the refusal never repeats it.
_A_MISPLACED_WORD = "a-word-that-must-not-leak"


def _run(
    console_script: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the console script of this environment
        [str(console_script), *arguments],
        env=measured({"PATH": "/usr/bin:/bin"}),
        capture_output=True,
        text=True,
        timeout=PATIENCE.total_seconds(),
        check=False,
    )


@pytest.mark.parametrize(
    ("console_script", "arguments", "argument"),
    [
        pytest.param(CONSOLE_ENTRY_POINT, ["unpin-host"], "host", id="no host"),
        pytest.param(CONSOLE_ENTRY_POINT, ["unpin-host", ""], "host", id="empty host"),
        pytest.param(CONSOLE_ENTRY_POINT, ["unpin-host", " "], "host", id="blank host"),
        pytest.param(CONSOLE_ENTRY_POINT, [_A_MISPLACED_WORD], "command", id="command"),
        pytest.param(WORKER_CONSOLE_ENTRY_POINT, [], "--port,--key-file", id="serve"),
        pytest.param(WORKER_CONSOLE_ENTRY_POINT, ["--ready"], "--port", id="ready"),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT, ["--port", "1"], "--key-file", id="no key"
        ),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT,
            ["--check", "--port", _A_MISPLACED_WORD],
            "--port",
            id="port no number",
        ),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT,
            ["--ready", "--port", "65536"],
            "--port",
            id="port out of range",
        ),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT, ["--port", "0"], "--port", id="serve port 0"
        ),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT,
            ["--ready", "--port", "0"],
            "--port",
            id="ready port 0",
        ),
    ],
)
def test_a_refused_argument_ends_the_script_in_one_line_naming_it(
    console_script: Path, arguments: list[str], argument: str
) -> None:
    ended = _run(console_script, arguments)

    assert ended.returncode == ARGUMENT_REFUSED_EXIT_STATUS
    assert ended.stderr == ""
    [line] = json_lines(ended.stdout)
    assert (line["event"], line["reason"], line["argument"]) == (
        ServerEvent.PROCESS_FAILED,
        ServerReason.ARGUMENT_REFUSED,
        argument,
    )
    assert _A_MISPLACED_WORD not in ended.stdout


@pytest.mark.parametrize(
    ("console_script", "arguments"),
    [
        pytest.param(CONSOLE_ENTRY_POINT, [f"--{_A_MISPLACED_WORD}"], id="option"),
        pytest.param(
            CONSOLE_ENTRY_POINT,
            ["unpin-host", "git.example", _A_MISPLACED_WORD],
            id="word",
        ),
        pytest.param(CONSOLE_ENTRY_POINT, ["migrate", _A_MISPLACED_WORD], id="migrate"),
        pytest.param(
            WORKER_CONSOLE_ENTRY_POINT, ["--check", _A_MISPLACED_WORD], id="worker"
        ),
    ],
)
def test_a_word_no_argument_takes_ends_the_script_in_one_line_without_it(
    console_script: Path, arguments: list[str]
) -> None:
    ended = _run(console_script, arguments)

    assert ended.returncode == ARGUMENT_REFUSED_EXIT_STATUS
    assert ended.stderr == ""
    [line] = json_lines(ended.stdout)
    assert (line["event"], line["reason"]) == (
        ServerEvent.PROCESS_FAILED,
        ServerReason.ARGUMENT_UNKNOWN,
    )
    assert _A_MISPLACED_WORD not in ended.stdout


@pytest.mark.parametrize(
    "console_script",
    [
        pytest.param(CONSOLE_ENTRY_POINT, id="presentator"),
        pytest.param(WORKER_CONSOLE_ENTRY_POINT, id="presentator-worker"),
    ],
)
def test_help_is_text_for_the_person_who_asked_and_exits_0(
    console_script: Path,
) -> None:
    ended = _run(console_script, ["--help"])

    assert ended.returncode == 0
    assert ended.stderr == ""
    assert ended.stdout.strip()


def test_the_unpin_host_usage_names_the_host_as_required() -> None:
    ended = _run(CONSOLE_ENTRY_POINT, ["unpin-host", "--help"])

    assert ended.returncode == 0
    [usage, *_] = ended.stdout.splitlines()
    assert usage.split()[-1] == "host"
