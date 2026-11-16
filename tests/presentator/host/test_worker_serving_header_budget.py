"""The header budget covers the gate and the release; a stall ends or keeps the life."""

from pathlib import Path

import pytest

from presentator.host.worker_serving import HEADER_SECONDS, LifeEnd
from tests.support.worker_accept_support import (
    accept_from,
    reading_after_a_proven_hello,
)
from tests.support.worker_support import (
    ANSWERED_HEADER,
    EXPIRED_AT_RELEASE,
    EXPIRED_BEFORE_RELEASE,
    ScriptedLife,
    StopServingError,
    listening_with,
    proven,
)


@pytest.mark.parametrize(
    ("inspection_takes", "release_takes", "events", "budgets"),
    [
        (HEADER_SECONDS, 0.0, [*EXPIRED_BEFORE_RELEASE, "cancel"], []),
        (HEADER_SECONDS + 1, 0.0, [*EXPIRED_BEFORE_RELEASE, "cancel"], []),
        (0.0, HEADER_SECONDS, [*EXPIRED_AT_RELEASE, "cancel"], []),
        (HEADER_SECONDS * 0.25, 0.0, ANSWERED_HEADER, [HEADER_SECONDS * 0.75]),
    ],
    ids=[
        "an inspection that spends the whole budget",
        "an inspection that overruns the budget",
        "a release that spends the rest of the budget",
        "an inspection that leaves a quarter of the budget spent",
    ],
)
def test_the_header_budget_covers_the_gate_and_the_release_as_well_as_the_wait(
    volume: Path,
    inspection_takes: float,
    release_takes: float,
    events: list[str],
    budgets: list[float],
) -> None:
    life = ScriptedLife(inspection_takes=inspection_takes, release_takes=release_takes)
    with listening_with(volume, accepts=1) as listener:
        what_the_client_read = reading_after_a_proven_hello(volume)
        with pytest.raises(StopServingError):
            accept_from(life, listener)
        assert what_the_client_read() == b""

    assert life.events == events
    assert life.budgets_waited == budgets


@pytest.mark.parametrize("sweep_quiet", [True, False], ids=["quiet", "not quiet"])
def test_the_life_is_kept_after_an_expiry_only_when_the_sweep_is_quiet(
    volume: Path, *, sweep_quiet: bool
) -> None:
    life = ScriptedLife(inspection_takes=HEADER_SECONDS, sweep_quiet=sweep_quiet)
    with listening_with(volume, accepts=1) as listener:
        proven(volume)
        if sweep_quiet:
            with pytest.raises(StopServingError):
                accept_from(life, listener)
        else:
            assert accept_from(life, listener) == LifeEnd.SWEEP

    assert ("cancel" in life.events) == sweep_quiet


@pytest.mark.parametrize("sweep_quiet", [True, False], ids=["quiet", "not quiet"])
def test_a_stall_that_spends_the_header_budget_before_the_launch_spawns_no_idle_child(
    volume: Path, *, sweep_quiet: bool
) -> None:
    life = ScriptedLife(arm_takes=HEADER_SECONDS, sweep_quiet=sweep_quiet)
    with listening_with(volume, accepts=1) as listener:
        what_the_client_read = reading_after_a_proven_hello(volume)
        if sweep_quiet:
            with pytest.raises(StopServingError):
                accept_from(life, listener)
        else:
            assert accept_from(life, listener) == LifeEnd.SWEEP
        assert what_the_client_read() == b""

    assert life.spawned == []
    assert ("cancel" in life.events) == sweep_quiet
