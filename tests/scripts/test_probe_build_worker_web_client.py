"""The web probe's client times a step's admission from the run directory it watches."""

import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from presentator.adapters import build_worker
from presentator.adapters.build_wire import StepHeader
from presentator.contracts.decks import StepKind
from tests.presentator.adapters.support.build_worker_support import (
    BUILT_BY_STEP,
    a_step,
    answering,
    fake_worker,
    toolchain,
)

OBSERVATION_SECONDS = 10


def test_a_step_a_worker_admitted_reports_when_and_leaves_the_client_unchanged(
    volume: Path,
    tmp_path: Path,
    load_script: Callable[..., ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    web_client = load_script("web_client", "probe_build_worker")
    exchange = build_worker.exchange_with_worker
    observed = threading.Event()
    first_seen = web_client.first_seen

    def observing(directory: Path, *, until: threading.Event) -> float | None:
        try:
            return first_seen(directory, until=until)
        finally:
            observed.set()

    monkeypatch.setattr(web_client, "first_seen", observing)
    replies: list[tuple[bool, float]] = []

    def building(connection: socket.socket, step: StepHeader) -> None:
        """A worker that answers only once the probe has seen its admission."""
        replies.append((observed.wait(OBSERVATION_SECONDS), time.time()))
        connection.sendall(BUILT_BY_STEP[step.step])

    with fake_worker(volume, answering(building)):
        before = time.time()
        failure, admitted = web_client.ran_timed(toolchain(volume), a_step(tmp_path))
    [(seen_before_reply, replied)] = replies
    assert failure is None
    assert seen_before_reply
    assert admitted is not None
    assert before <= admitted <= replied
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
