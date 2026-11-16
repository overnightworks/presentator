"""Two processes on one Redis: the lease of a session is held by one at a time."""

import subprocess
from collections.abc import Iterator
from threading import Event

import pytest

from tests.presentator.adapters.support.ask_lease_process import (
    BUSY,
    LEASE_VARIABLE,
    RELEASED,
    TAKEN,
    URL_VARIABLE,
)
from tests.support.two_processes import TwoProcesses, two_modules

_LEASE_MILLISECONDS = 2000
_WAIT_FOR_EXPIRY_SECONDS = 30
_POLL_SECONDS = 0.1


def _say(process: subprocess.Popen[str], command: str) -> str:
    assert process.stdin is not None
    assert process.stdout is not None
    process.stdin.write(f"{command}\n")
    process.stdin.flush()
    return process.stdout.readline().strip()


@pytest.fixture
def processes(redis_url: str) -> Iterator[TwoProcesses]:
    with two_modules(
        "tests.presentator.adapters.support.ask_lease_process",
        [],
        {URL_VARIABLE: redis_url, LEASE_VARIABLE: str(_LEASE_MILLISECONDS)},
    ) as started:
        yield started


def test_an_ask_taken_at_one_process_makes_the_other_busy(
    processes: TwoProcesses,
) -> None:
    taken = _say(processes.first, "admit")

    other = _say(processes.second, "admit")

    assert (taken, other) == (TAKEN, BUSY)


def test_the_lease_is_free_at_the_other_process_once_its_holder_releases(
    processes: TwoProcesses,
) -> None:
    _say(processes.first, "admit")
    released = _say(processes.first, "release")

    other = _say(processes.second, "admit")

    assert (released, other) == (RELEASED, TAKEN)


def test_the_lease_of_a_holder_that_died_expires_at_the_other_process(
    processes: TwoProcesses,
) -> None:
    _say(processes.first, "admit")
    processes.first.kill()
    processes.first.wait()

    answers = [_say(processes.second, "admit")]
    waited = 0.0
    while answers[-1] == BUSY and waited < _WAIT_FOR_EXPIRY_SECONDS:
        Event().wait(_POLL_SECONDS)
        waited += _POLL_SECONDS
        answers.append(_say(processes.second, "admit"))

    assert answers[0] == BUSY
    assert answers[-1] == TAKEN
