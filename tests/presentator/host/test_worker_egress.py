"""The build worker refuses to start while a process of it can send off the loopback."""

from dataclasses import replace
from pathlib import Path

import pytest

from presentator.adapters import worker_children
from presentator.host import worker_self_check
from presentator.host.worker_self_check import (
    PROBED_IDENTITIES,
    SEND_DROPPED,
    Predicate,
)
from tests.presentator.host.support.sealed_snapshot import (
    read_sealed_snapshot,
    read_staged_root_as_sealed_process,
    sealed_sends,
    sealed_snapshot,
    with_mounts,
    with_resource_bounds,
)

SENT = "sent"


def judged(sends: tuple[str, ...]) -> Predicate | None:
    return worker_self_check.first_failing_predicate(
        replace(sealed_snapshot(), sends=sends)
    )


def one_probe_sends(position: int) -> tuple[str, ...]:
    sends = list(sealed_sends())
    sends[position] = SENT
    return tuple(sends)


def test_every_probed_identity_dropped_passes_every_predicate() -> None:
    assert judged(sealed_sends()) is None


@pytest.mark.parametrize("position", range(len(PROBED_IDENTITIES)))
def test_one_identity_that_can_send_refuses_as_egress(position: int) -> None:
    assert judged(one_probe_sends(position)) == Predicate.EGRESS


@pytest.mark.parametrize(
    "sends",
    [(), (SEND_DROPPED,), ("ENETUNREACH",) * len(PROBED_IDENTITIES), ("",) * 3],
    ids=["no probe", "too few probes", "another error", "no answer"],
)
def test_anything_but_a_drop_of_every_probe_refuses_as_egress(
    sends: tuple[str, ...],
) -> None:
    assert judged(sends) == Predicate.EGRESS


def test_egress_is_judged_after_mounts_and_before_resource_bounds() -> None:
    snapshot = with_resource_bounds(memory_max="max")(
        with_mounts(root_read_only=False)(
            replace(sealed_snapshot(), sends=one_probe_sends(0))
        )
    )

    assert worker_self_check.first_failing_predicate(snapshot) == Predicate.MOUNTS
    mounted = with_mounts(root_read_only=True)(snapshot)
    assert worker_self_check.first_failing_predicate(mounted) == Predicate.EGRESS


def test_an_unreadable_probe_marks_egress_unreadable(sealed_root: Path) -> None:
    def failing() -> tuple[str, ...]:
        message = "setuid"
        raise PermissionError(message)

    snapshot = read_sealed_snapshot(sealed_root, failing)

    assert snapshot.unreadable == {Predicate.EGRESS}
    sealed = replace(sealed_snapshot(), unreadable=snapshot.unreadable)
    assert worker_self_check.first_failing_predicate(sealed) == Predicate.EGRESS


def test_a_second_link_listed_only_in_the_device_table_is_not_judged_by_mounts(
    sealed_root: Path,
) -> None:
    (sealed_root / "proc/net").mkdir(parents=True)
    (sealed_root / "proc/net/dev").write_text(
        "Inter-|\n face |\n    lo: 0\n  eth0: 0\n"
    )

    snapshot = read_staged_root_as_sealed_process(sealed_root)

    assert snapshot.unreadable == frozenset()
    assert worker_self_check.first_failing_predicate(snapshot) is None


@pytest.mark.parametrize(
    ("program", "answer"),
    [("print(' sent ')", "sent"), ("import time; time.sleep(5)", "timeout")],
    ids=["its output", "outlasting its seconds"],
)
def test_a_program_run_for_a_probe_answers_what_it_printed_or_a_timeout(
    program: str, answer: str
) -> None:
    assert worker_children.program_output_as(program, None, seconds=0.5) == answer


def test_one_probe_runs_for_each_probed_identity_and_answers_in_their_order() -> None:
    asked: list[tuple[int, int] | None] = []

    def run_program(
        _program: str, identity: tuple[int, int] | None, **_limits: float
    ) -> str:
        asked.append(identity)
        return f"answer {len(asked)}"

    answers = worker_self_check.send_probes(run_program)

    assert asked == list(PROBED_IDENTITIES)
    assert answers == tuple(f"answer {n}" for n in range(1, len(asked) + 1))
