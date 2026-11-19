"""A console script refuses a wrong or missing argument in one coded JSON line."""

import logging
import subprocess
from pathlib import Path

import pytest

from presentator.host.command_line import read_the_command_line
from presentator.host.lifecycle import (
    ARGUMENT_REFUSED_EXIT_STATUS,
    ServerEvent,
    ServerReason,
)
from tests.presentator.application.support.fakes import PATIENCE
from tests.support.log_lines import json_lines
from tests.support.two_processes import CONSOLE_ENTRY_POINT, WORKER_CONSOLE_ENTRY_POINT

# A word that stands where a secret could: the refusal never repeats it.
_A_MISPLACED_WORD = "a-word-that-must-not-leak"


def _run(
    console_script: Path, arguments: list[str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the console script of this environment
        [str(console_script), *arguments],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=PATIENCE.total_seconds(),
        check=False,
    )


@pytest.mark.parametrize(
    ("console_script", "arguments", "argument"),
    [
        pytest.param(CONSOLE_ENTRY_POINT, ["unpin-host"], "host", id="no host"),
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
    ("arguments", "reason"),
    [
        pytest.param(["unpin-host"], ServerReason.ARGUMENT_REFUSED, id="no host"),
        pytest.param(
            [_A_MISPLACED_WORD], ServerReason.ARGUMENT_REFUSED, id="wrong command"
        ),
        pytest.param(
            [f"--{_A_MISPLACED_WORD}"], ServerReason.ARGUMENT_UNKNOWN, id="option"
        ),
    ],
)
def test_reading_a_wrong_command_line_ends_in_the_refusal_naming_its_reason(
    arguments: list[str], reason: ServerReason, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as ended:
        read_the_command_line(arguments)

    assert ended.value.code == ARGUMENT_REFUSED_EXIT_STATUS
    [record] = caplog.records
    assert (record.getMessage(), record.__dict__["reason"]) == (
        ServerEvent.PROCESS_FAILED,
        reason,
    )


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
