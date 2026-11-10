"""The worker lowers its process limit under Kubernetes and judges it."""

import resource
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.host import worker
from presentator.host.worker import Boundary
from presentator.host.worker_self_check import (
    MAXIMUM_PROCESSES,
    PROCESS_LIMITS_FILE,
    Predicate,
    Seal,
    first_failing_predicate,
    lowered_process_limit,
)
from tests.presentator.host.support.sealed_snapshot import (
    limits_file,
    read_sealed_snapshot,
    sealed_snapshot,
    with_resource_bounds,
)
from tests.presentator.host.support.worker_boundary import boundary_over

UNLIMITED = "unlimited"
MAXIMUM = str(MAXIMUM_PROCESSES)
ABOVE_MAXIMUM = str(MAXIMUM_PROCESSES + 1)
BOUND = Predicate.RESOURCE_BOUNDS


def judged(seal: Seal, soft: str, hard: str, **changes: str) -> Predicate | None:
    snapshot = with_resource_bounds(process_limit=(soft, hard), **changes)(
        replace(sealed_snapshot(), seal=seal)
    )
    return first_failing_predicate(snapshot)


COMPOSE_ROWS = {
    "an unlimited process limit": (UNLIMITED, UNLIMITED, MAXIMUM, None),
    "an unlimited pid bound": (MAXIMUM, MAXIMUM, "max", BOUND),
    "a pid bound above the maximum": (MAXIMUM, MAXIMUM, ABOVE_MAXIMUM, BOUND),
}


@pytest.mark.parametrize(
    ("soft", "hard", "pids_max", "refused"),
    COMPOSE_ROWS.values(),
    ids=COMPOSE_ROWS.keys(),
)
def test_compose_judges_the_pid_bound_and_not_the_process_limit(
    soft: str, hard: str, pids_max: str, refused: Predicate | None
) -> None:
    assert judged(Seal.COMPOSE, soft, hard, pids_max=pids_max) == refused


KUBERNETES_ROWS: dict[str, tuple[str, str, dict[str, str], Predicate]] = {
    "at the maximum without a pid bound": (
        MAXIMUM,
        MAXIMUM,
        {"pids_max": "max"},
        Predicate.KUBERNETES_UNPROVEN,
    ),
    "below the maximum": (
        "100",
        "256",
        {},
        Predicate.KUBERNETES_UNPROVEN,
    ),
    "an unlimited soft limit": (UNLIMITED, MAXIMUM, {}, BOUND),
    "an unlimited hard limit": (MAXIMUM, UNLIMITED, {}, BOUND),
    "a soft limit above the maximum": (ABOVE_MAXIMUM, MAXIMUM, {}, BOUND),
    "a hard limit above the maximum": (MAXIMUM, ABOVE_MAXIMUM, {}, BOUND),
    "an unlimited memory bound": (MAXIMUM, MAXIMUM, {"memory_max": "max"}, BOUND),
}


@pytest.mark.parametrize(
    ("soft", "hard", "changes", "refused"),
    KUBERNETES_ROWS.values(),
    ids=KUBERNETES_ROWS.keys(),
)
def test_kubernetes_judges_the_process_limit_and_the_memory_bound(
    soft: str, hard: str, changes: dict[str, str], refused: Predicate
) -> None:
    assert judged(Seal.KUBERNETES, soft, hard, **changes) == refused


def test_a_missing_pid_bound_is_unreadable_under_kubernetes(sealed_root: Path) -> None:
    (sealed_root / "sys/fs/cgroup/pids.max").unlink()

    snapshot = read_sealed_snapshot(sealed_root, seal=Seal.KUBERNETES)

    assert snapshot.unreadable == {BOUND}


def test_the_process_limit_is_read_from_the_max_processes_row(
    sealed_root: Path,
) -> None:
    (sealed_root / PROCESS_LIMITS_FILE).write_text(limits_file("100", UNLIMITED))

    snapshot = read_sealed_snapshot(sealed_root)

    assert snapshot.resource_bounds.process_limit == ("100", UNLIMITED)
    assert snapshot.unreadable == frozenset()


@pytest.mark.parametrize(
    "content",
    [
        None,
        limits_file("1", "1").replace("Max processes", "Max pipes"),
        "Limit\nMax processes\n",
        limits_file("1", ""),
    ],
    ids=["no file", "no row", "no values", "one value"],
)
def test_an_unreadable_process_limit_marks_the_resource_bounds_unreadable(
    sealed_root: Path, content: str | None
) -> None:
    limits = sealed_root / PROCESS_LIMITS_FILE
    if content is None:
        limits.unlink()
    else:
        limits.write_text(content)

    assert read_sealed_snapshot(sealed_root).unreadable == {BOUND}


INFINITY = resource.RLIM_INFINITY
LOWERINGS = {
    "unlimited both": ((INFINITY, INFINITY), (MAXIMUM_PROCESSES, MAXIMUM_PROCESSES)),
    "soft above, hard unlimited": (
        (MAXIMUM_PROCESSES + 88, INFINITY),
        (MAXIMUM_PROCESSES, MAXIMUM_PROCESSES),
    ),
    "soft below, hard unlimited": (
        (100, INFINITY),
        (100, MAXIMUM_PROCESSES),
    ),
    "both below": ((100, 256), (100, 256)),
    "both at the maximum": (
        (MAXIMUM_PROCESSES, MAXIMUM_PROCESSES),
        (MAXIMUM_PROCESSES, MAXIMUM_PROCESSES),
    ),
}


@pytest.mark.parametrize(
    ("limits", "lowered"), LOWERINGS.values(), ids=LOWERINGS.keys()
)
def test_the_lowering_caps_at_the_maximum_and_never_raises(
    limits: tuple[int, int], lowered: tuple[int, int]
) -> None:
    assert lowered_process_limit(*limits) == lowered


def test_the_effect_reads_then_sets_the_lowered_limit() -> None:
    calls: list[tuple[object, ...]] = []

    def get_limit() -> tuple[int, int]:
        calls.append(("get",))
        return INFINITY, INFINITY

    def set_limit(limits: tuple[int, int]) -> None:
        calls.append(("set", limits))

    worker.lower_process_limit(get_limit, set_limit)

    assert calls == [("get",), ("set", (MAXIMUM_PROCESSES, MAXIMUM_PROCESSES))]


def recording_boundary(
    seal: Seal, events: list[str], lowering: Exception | None = None
) -> Boundary:
    def lower() -> None:
        events.append("lower")
        if lowering:
            raise lowering

    return replace(
        boundary_over(sealed_snapshot(), events),
        read_seal=lambda: seal,
        lower_process_limit=lower,
    )


@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_kubernetes_lowers_the_limit_after_the_seal_and_before_the_snapshot(
    arguments: list[str],
) -> None:
    events: list[str] = []

    status = worker.main(arguments, recording_boundary(Seal.KUBERNETES, events))

    assert status == worker.REFUSAL_EXIT_STATUS
    assert events == ["prctl", "umask", "lower", "read"]


@pytest.mark.parametrize(
    ("arguments", "events"),
    [
        ([], ["prctl", "umask", "read", "settings", "serve"]),
        (["--check"], ["prctl", "umask", "read"]),
    ],
)
def test_compose_never_lowers_the_limit(
    arguments: list[str], events: list[str]
) -> None:
    recorded: list[str] = []

    status = worker.main(arguments, recording_boundary(Seal.COMPOSE, recorded))

    assert (status, recorded) == (0, events)


@pytest.mark.parametrize("failure", [OSError("denied"), ValueError("not allowed")])
@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_a_failed_lowering_refuses_the_start_before_the_snapshot_is_read(
    caplog: pytest.LogCaptureFixture, failure: Exception, arguments: list[str]
) -> None:
    events: list[str] = []

    status = worker.main(
        arguments, recording_boundary(Seal.KUBERNETES, events, failure)
    )

    assert status == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {BOUND}" in caplog.text
    assert events == ["prctl", "umask", "lower"]
