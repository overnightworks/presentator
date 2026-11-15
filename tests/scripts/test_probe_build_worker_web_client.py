"""The web probe's client times a step's admission from the run directory it watches."""

import socket
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from presentator.adapters import build_worker
from presentator.adapters.build_wire import StepHeader
from presentator.contracts.decks import StepKind
from tests.presentator.adapters.support.build_worker_support import (
    BUILT_BY_STEP,
    a_step,
    toolchain,
)
from tests.presentator.adapters.test_build_worker import answering, fake_worker

BUILD_SECONDS = 0.3


def building(connection: socket.socket, step: StepHeader) -> None:
    """A worker that takes its time to build, as a real one does."""
    time.sleep(BUILD_SECONDS)
    connection.sendall(BUILT_BY_STEP[step.step])


def test_a_step_a_worker_admitted_reports_when_and_leaves_the_client_unchanged(
    volume: Path, tmp_path: Path, load_script: Callable[..., ModuleType]
) -> None:
    web_client = load_script("web_client", "probe_build_worker")
    exchange = build_worker.exchange_with_worker
    with fake_worker(volume, answering(building)):
        before = time.time()
        failure, admitted = web_client.ran_timed(toolchain(volume), a_step(tmp_path))
        after = time.time()
    assert failure is None
    assert admitted is not None
    assert before <= admitted <= after - BUILD_SECONDS / 2
    assert build_worker.exchange_with_worker is exchange


def test_a_step_no_worker_answered_reports_no_admission(
    volume: Path, tmp_path: Path, load_script: Callable[..., ModuleType]
) -> None:
    web_client = load_script("web_client", "probe_build_worker")
    unavailable, admitted = web_client.ran_timed(
        toolchain(volume, connect_window_seconds=0.1),
        a_step(tmp_path, StepKind.EXPORT),
    )
    assert admitted is None
    assert unavailable is not None
