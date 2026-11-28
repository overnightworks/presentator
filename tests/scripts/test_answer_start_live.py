"""The live answer start command: gathers both services' logs, posts the figure."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.scripts.support.answer_start_lines import answer, speech_end

if TYPE_CHECKING:
    from collections.abc import Callable

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
_COMMAND = _SCRIPTS / "answer_start_live"
_ITEM = "944"

_FAKE_K3S = """#!/bin/sh
echo k3s >> "$FAKE_LOGS_DIR/calls"
case "$*" in
  *presentator*) [ "$FAKE_K3S_FAILS" != web ] || exit 7;;
esac
case "$*" in
  *presentator*) cat "$FAKE_LOGS_DIR/web" ;;
  *) [ "$FAKE_K3S_FAILS" != speech ] || exit 7; cat "$FAKE_LOGS_DIR/speech" ;;
esac
"""
_FAKE_GH = """#!/bin/sh
echo gh >> "$FAKE_LOGS_DIR/calls"
printf '%s %s %s %s' "$1" "$2" "$3" "$4" > "$FAKE_LOGS_DIR/gh_arguments"
printf '%s' "$5" > "$FAKE_LOGS_DIR/gh_body"
echo "https://example.invalid/comment"
"""

_UV_BUILD_LINE = "Building the project"
# Like uv, it reports a build on stderr unless quiet (-q or --quiet), then runs python.
_FAKE_UV = f"""#!/bin/sh
quiet=no
for argument in "$@"; do
  case "$argument" in --quiet|-q) quiet=yes ;; esac
done
[ "$quiet" = yes ] || echo "{_UV_BUILD_LINE}" >&2
while [ "$1" != python ]; do shift; done
shift
exec "$FAKE_UV_PYTHON" "$@" $FAKE_UV_EXTRA_ARGUMENTS
"""


def _joined_turn_logs() -> tuple[str, str]:
    """The app's and the speech service's line of one turn, joined by trace."""
    return answer("a", asked_at_ms=1200), speech_end("a", at_ms=0)


def _figure(*lines: str) -> str:
    """What `answer_start.py` prints for the lines."""
    read = subprocess.run(  # noqa: S603 - the interpreter and this repository's own script
        [sys.executable, str(_SCRIPTS / "answer_start.py")],
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
    for name, body in (("k3s", _FAKE_K3S), ("gh", _FAKE_GH), ("uv", _FAKE_UV)):
        (fakes / name).write_text(body)
        (fakes / name).chmod(0o755)
    monkeypatch.setenv("PATH", f"{fakes}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_LOGS_DIR", str(tmp_path))
    monkeypatch.setenv("FAKE_UV_PYTHON", sys.executable)

    def run(
        web: str,
        speech: str,
        *,
        k3s_fails: str = "",
        arguments: tuple[str, ...] = (_ITEM,),
        read_arguments: str = "",
    ) -> subprocess.CompletedProcess[str]:
        (tmp_path / "web").write_text(web)
        (tmp_path / "speech").write_text(speech)
        monkeypatch.setenv("FAKE_K3S_FAILS", k3s_fails)
        monkeypatch.setenv("FAKE_UV_EXTRA_ARGUMENTS", read_arguments)
        return subprocess.run(  # noqa: S603 - this repository's own script
            [str(_COMMAND), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def _calls(tmp_path: Path) -> str:
    """Which fake tools the command called."""
    calls = tmp_path / "calls"
    return calls.read_text() if calls.exists() else ""


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


def test_the_posted_figure_carries_no_line_of_uv(
    run_live: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    web, speech = _joined_turn_logs()

    run_live(web, speech)

    assert _UV_BUILD_LINE not in _posted_body(tmp_path)


def test_a_failing_read_reports_its_own_error_and_no_line_of_uv(
    run_live: Callable[..., subprocess.CompletedProcess[str]], tmp_path: Path
) -> None:
    web, speech = _joined_turn_logs()

    done = run_live(web, speech, read_arguments="--unknown-option")

    assert done.returncode != 0
    assert "unrecognized arguments" in done.stderr
    assert _UV_BUILD_LINE not in done.stderr
    assert not (tmp_path / "gh_body").exists()


@pytest.mark.parametrize("failing_read", ["web", "speech"])
def test_a_failing_cluster_read_fails_the_command_and_posts_nothing(
    run_live: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
    failing_read: str,
) -> None:
    web, speech = _joined_turn_logs()

    done = run_live(web, speech, k3s_fails=failing_read)

    assert done.returncode != 0
    assert not (tmp_path / "gh_body").exists()


@pytest.mark.parametrize(
    "arguments", [(), ("a",), ("1", "2")], ids=["none", "not-a-number", "two"]
)
def test_the_command_refuses_anything_but_one_item_number_and_calls_nothing(
    run_live: Callable[..., subprocess.CompletedProcess[str]],
    tmp_path: Path,
    arguments: tuple[str, ...],
) -> None:
    done = run_live("", "", arguments=arguments)

    assert (done.returncode, "usage" in done.stderr, _calls(tmp_path)) == (2, True, "")
