"""The probe driver refuses a stalled client instead of waiting for it."""

import subprocess
import sys
from collections.abc import Callable, Iterator
from types import ModuleType

import pytest

SILENT_AFTER_PARTIAL_LINE = (
    "import sys, time; sys.stdout.write('upl'); sys.stdout.flush(); time.sleep(60)"
)
TWO_LINES = "print('uploaded'); print('closed 1.5', flush=True)"


@pytest.fixture
def lives(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_lives")


@pytest.fixture
def start() -> Iterator[Callable[[str], subprocess.Popen[str]]]:
    clients: list[subprocess.Popen[str]] = []

    def launch(code: str) -> subprocess.Popen[str]:
        client = subprocess.Popen(  # noqa: S603
            (sys.executable, "-c", code), stdout=subprocess.PIPE, text=True
        )
        clients.append(client)
        return client

    yield launch
    for client in clients:
        client.kill()
        client.communicate()


def test_a_partial_handshake_line_that_stalls_is_refused_as_not_driven(
    lives: ModuleType, start: Callable[[str], subprocess.Popen[str]]
) -> None:
    """defect:#455 - a stalled partial line must refuse, not hang."""
    client = start(SILENT_AFTER_PARTIAL_LINE)
    assert client.stdout is not None

    with pytest.raises(SystemExit):
        lives.handshake_line(client, client.stdout, deadline_seconds=1)

    client.wait(timeout=10)


def test_consecutive_handshake_lines_are_each_returned(
    lives: ModuleType, start: Callable[[str], subprocess.Popen[str]]
) -> None:
    """defect:#455 - unbuffered reads lose no later line."""
    client = start(TWO_LINES)
    assert client.stdout is not None

    lines = [
        lives.handshake_line(client, client.stdout),
        lives.handshake_line(client, client.stdout),
    ]
    client.wait(timeout=10)

    assert lines == ["uploaded", "closed 1.5"]
