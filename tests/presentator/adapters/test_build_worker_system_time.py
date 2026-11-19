"""The build worker client's default time: the process clock and a real sleep."""

import math
from pathlib import Path
from time import monotonic

from presentator.contracts.decks import ToolchainUnavailable
from tests.presentator.adapters.support.build_worker_support import (
    a_step,
    counting,
    fake_worker,
    hanging_up,
    toolchain,
)


def test_a_client_built_without_a_time_waits_its_retries_out_on_the_process_clock(
    volume: Path, tmp_path: Path
) -> None:
    window = 0.2
    first_retry = 0.02
    client = toolchain(
        volume,
        on_the_process_clock=True,
        connect_window_seconds=window,
        first_retry_seconds=first_retry,
        longest_retry_seconds=0.05,
    )
    closings: list[None] = []
    started = monotonic()

    with fake_worker(volume, counting(closings, hanging_up), accepts=50):
        result = client.ran(a_step(tmp_path))

    assert result == ToolchainUnavailable()
    assert monotonic() - started >= window
    assert len(closings) <= math.ceil(window / first_retry) + 1
