"""The live answer start command: gathers both services' logs, posts the figure."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.scripts.conftest import SCRIPTS

if TYPE_CHECKING:
    from collections.abc import Callable

_COMMAND = SCRIPTS / "answer_start_live"
_ITEM = "944"
_NOON = datetime(2026, 11, 19, 12, tzinfo=UTC)

_FAKE_K3S = """#!/bin/sh
[ -z "$FAKE_K3S_FAILS" ] || exit 7
case "$*" in
  *presentator*) cat "$FAKE_LOGS_DIR/web" ;;
  *) cat "$FAKE_LOGS_DIR/speech" ;;
esac
"""
_FAKE_GH = """#!/bin/sh
printf '%s %s %s %s' "$1" "$2" "$3" "$4" > "$FAKE_LOGS_DIR/gh_arguments"
printf '%s' "$5" > "$FAKE_LOGS_DIR/gh_body"
echo "https://example.invalid/comment"
"""


def _joined_turn_logs() -> tuple[str, str]:
    """The speech service's and the app's line of one turn, joined by trace."""
    speech = {
        "ts": (_NOON + timedelta(milliseconds=500)).isoformat(),
        "event": "turn_end_wait.ended",
        "trace_id": "a",
        "duration_ms": 500.0,
    }
    web = {
        "ts": (_NOON + timedelta(milliseconds=4200)).isoformat(),
        "event": "answer.ended",
        "trace_id": "a",
        "duration_ms": 3000.0,
        "outcome": "ok",
        "first_output_ms": 900.0,
    }
    return json.dumps(web), json.dumps(speech)


def _figure(*lines: str) -> str:
    """What `answer_start.py` prints for the lines."""
    read = subprocess.run(  # noqa: S603 - the interpreter and this repository's own script
        [sys.executable, str(SCRIPTS / "answer_start.py")],
        input="\n".join(lines),
        capture_output=True,
        text=True,
        check=False,
    )
    return read.stdout.strip()


@pytest.fixture
def run_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Run the command with fake `k3s` and `gh` first on PATH."""
    fakes = tmp_path / "bin"
    fakes.mkdir()
    for name, body in (("k3s", _FAKE_K3S), ("gh", _FAKE_GH)):
        (fakes / name).write_text(body)
        (fakes / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakes}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOGS_DIR", str(tmp_path))

    def run(
        web: str, speech: str, *, k3s_fails: bool = False
    ) -> subprocess.CompletedProcess[str]:
        (tmp_path / "web").write_text(web)
        (tmp_path / "speech").write_text(speech)
        monkeypatch.setenv("FAKE_K3S_FAILS", "1" if k3s_fails else "")
        return subprocess.run(  # noqa: S603 - this repository's own script
            [str(_COMMAND), _ITEM],
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def _posted_body(tmp_path: Path) -> str:
    return (tmp_path / "gh_body").read_text()


def test_the_figure_of_the_joined_turns_is_posted_on_the_item(
    run_live: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    web, speech = _joined_turn_logs()

    done = run_live(web, speech)

    assert (done.returncode, done.stdout.strip()) == (
        0,
        "https://example.invalid/comment",
    )
    assert (tmp_path / "gh_arguments").read_text() == f"issue comment {_ITEM} --body"
    assert _posted_body(tmp_path) == _figure(web, speech)
    assert _posted_body(tmp_path).startswith("turns 1")


def test_no_log_lines_post_what_the_read_prints_for_no_turn(
    run_live: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    done = run_live("", "")

    assert done.returncode == 0
    assert _posted_body(tmp_path) == _figure("")


def test_a_failing_cluster_read_fails_the_command_and_posts_nothing(
    run_live: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    web, speech = _joined_turn_logs()

    done = run_live(web, speech, k3s_fails=True)

    assert done.returncode != 0
    assert not (tmp_path / "gh_body").exists()


@pytest.mark.parametrize(
    "arguments", [[], ["a"], ["1", "2"]], ids=["none", "not-a-number", "two"]
)
def test_the_command_refuses_anything_but_one_item_number(arguments: list[str]) -> None:
    done = subprocess.run(  # noqa: S603 - this repository's own script
        [str(_COMMAND), *arguments], capture_output=True, text=True, check=False
    )

    assert done.returncode != 0
