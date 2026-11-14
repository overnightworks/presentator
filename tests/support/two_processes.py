"""Two real `presentator` processes started together through the console entry point."""

import os
import subprocess
import sys
from collections.abc import Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

_FINISH_SECONDS = 60
_CONSOLE_ENTRY_POINT = Path(sys.executable).parent / "presentator"


@dataclass(frozen=True, slots=True)
class Finished:
    """How one process ended."""

    exit_code: int
    output: str


@dataclass(frozen=True, slots=True)
class TwoProcesses:
    """Two processes of the same command, running at once."""

    first: subprocess.Popen[str]
    second: subprocess.Popen[str]

    def finish(self) -> tuple[Finished, Finished]:
        """Wait for both to end."""
        return _finished(self.first), _finished(self.second)


@contextmanager
def two_presentators(
    arguments: Sequence[str], environment: Mapping[str, str]
) -> Generator[TwoProcesses]:
    """Start two processes of `presentator <arguments>` with only `environment`.

    None outlives the block: whatever still runs is killed and reaped.
    """
    with ExitStack() as processes:
        started = [
            processes.enter_context(
                subprocess.Popen(
                    [str(_CONSOLE_ENTRY_POINT), *arguments],
                    env={"PATH": os.environ["PATH"], **environment},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            )
            for _ in range(2)
        ]
        # Runs before the processes are waited for.
        processes.callback(lambda: [each.kill() for each in started])
        yield TwoProcesses(*started)


def _finished(process: subprocess.Popen[str]) -> Finished:
    output, _ = process.communicate(timeout=_FINISH_SECONDS)
    return Finished(process.returncode, output)
