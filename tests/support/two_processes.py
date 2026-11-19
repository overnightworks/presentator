"""Two real processes of one command, started together."""

import os
import subprocess
import sys
from collections.abc import Generator, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_FINISH_SECONDS = 60
CONSOLE_ENTRY_POINT: Final = Path(sys.executable).parent / "presentator"
WORKER_CONSOLE_ENTRY_POINT: Final = Path(sys.executable).parent / "presentator-worker"
# A module started with `-m` imports `tests.support` from here.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
READY: Final = "ready"
# Coverage's subprocess patch sets it in a measured run; a child given it is measured.
_COVERAGE_CONFIG: Final = "COVERAGE_PROCESS_CONFIG"


def measured(environment: Mapping[str, str]) -> dict[str, str]:
    """`environment`, measured by coverage as this run is, if it is."""
    measuring = os.environ.get(_COVERAGE_CONFIG)
    return {
        **environment,
        **({} if measuring is None else {_COVERAGE_CONFIG: measuring}),
    }


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


def two_presentators(
    arguments: Sequence[str], environment: Mapping[str, str]
) -> AbstractContextManager[TwoProcesses]:
    """Start two processes of `presentator <arguments>` with only `environment`."""
    return two_processes([str(CONSOLE_ENTRY_POINT), *arguments], environment)


def two_modules(
    module: str,
    arguments: Sequence[str],
    environment: Mapping[str, str],
    *,
    second_arguments: Sequence[str] | None = None,
    second_environment: Mapping[str, str] | None = None,
) -> AbstractContextManager[TwoProcesses]:
    """Start two processes of `python -m <module>` that can import `tests`.

    The second takes `second_arguments` and `second_environment` on top of
    `environment` when given, so the two do different things.
    """
    shared = {"PYTHONPATH": str(_REPOSITORY_ROOT), **environment}
    return _processes(
        [
            [sys.executable, "-m", module, *arguments],
            [
                sys.executable,
                "-m",
                module,
                *(arguments if second_arguments is None else second_arguments),
            ],
        ],
        shared,
        second_environment={**shared, **(second_environment or {})},
    )


def announce_ready_and_wait_for_go() -> None:
    """In a child: say it is connected, then block until the parent lets both go."""
    print(READY, flush=True)  # noqa: T201 - the parent reads this line
    sys.stdin.readline()


def release_together(processes: TwoProcesses) -> None:
    """In the parent: wait until both children are ready, then let both go at once."""
    for each in (processes.first, processes.second):
        assert each.stdout is not None
        assert each.stdout.readline().strip() == READY
    for each in (processes.first, processes.second):
        assert each.stdin is not None
        each.stdin.write("go\n")
        each.stdin.flush()


def two_processes(
    command: Sequence[str], environment: Mapping[str, str]
) -> AbstractContextManager[TwoProcesses]:
    """Start two processes of `command` with only `environment`."""
    return _processes([command, command], environment)


@contextmanager
def _processes(
    commands: Sequence[Sequence[str]],
    environment: Mapping[str, str],
    *,
    second_environment: Mapping[str, str] | None = None,
) -> Generator[TwoProcesses]:
    """Start one process per command; none outlives the block.

    The second command runs in `second_environment` when given. Whatever still
    runs is killed and reaped.
    """
    environments = [environment, second_environment or environment]
    with ExitStack() as processes:
        started = [
            processes.enter_context(
                subprocess.Popen(
                    command,
                    env={"PATH": os.environ["PATH"], **each_environment},
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
            )
            for command, each_environment in zip(commands, environments, strict=True)
        ]
        # Runs before the processes are waited for.
        processes.callback(lambda: [each.kill() for each in started])
        yield TwoProcesses(*started)


def _finished(process: subprocess.Popen[str]) -> Finished:
    output, _ = process.communicate(timeout=_FINISH_SECONDS)
    return Finished(process.returncode, output)
