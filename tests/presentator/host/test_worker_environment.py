"""The worker judges the environment's names, one set for every start."""

from dataclasses import replace
from pathlib import Path

import pytest

from presentator.host import worker, worker_self_check
from presentator.host.config import worker_settings_variables
from presentator.host.worker_self_check import (
    KUBERNETES_ENVIRONMENT_KEYS,
    TOLERATED_UNREAD_VARIABLE,
    Predicate,
    first_failing_predicate,
)
from tests.presentator.host.support.sealed_snapshot import (
    read_sealed_snapshot,
    read_staged_root_as_sealed_process,
    sealed_snapshot,
    with_fields,
)
from tests.presentator.host.support.worker_boundary import boundary_over

SEALED_KEYS = sealed_snapshot().environment_keys
OTHER_SERVICE_LINK = "REDIS_SERVICE_HOST"

KEYS = {
    "the sealed names": (SEALED_KEYS, None),
    "the kubelet names": (SEALED_KEYS | KUBERNETES_ENVIRONMENT_KEYS, None),
    "the platform's keys": (
        worker_settings_variables()
        | {TOLERATED_UNREAD_VARIABLE}
        | KUBERNETES_ENVIRONMENT_KEYS,
        None,
    ),
    "the unread name alone": (SEALED_KEYS | {TOLERATED_UNREAD_VARIABLE}, None),
    "another service's link": (
        SEALED_KEYS | {OTHER_SERVICE_LINK},
        Predicate.ENVIRONMENT,
    ),
}


@pytest.mark.parametrize(("keys", "refused"), KEYS.values(), ids=KEYS.keys())
def test_the_environment_names_decide_the_failing_predicate(
    keys: frozenset[str], refused: Predicate | None
) -> None:
    snapshot = with_fields(environment_keys=keys)(sealed_snapshot())

    assert first_failing_predicate(snapshot) == refused


@pytest.mark.parametrize("predicate", list(Predicate))
def test_an_unreadable_predicate_is_named(predicate: Predicate) -> None:
    snapshot = with_fields(unreadable=frozenset({predicate}))(sealed_snapshot())

    assert first_failing_predicate(snapshot) == predicate


@pytest.mark.parametrize("value", ["kubernetes", "compose", "COMPOSE", "k8s", ""])
@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_a_start_passes_with_the_unread_variable_set_to_any_value(
    sealed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: str,
    arguments: list[str],
) -> None:
    monkeypatch.setattr(
        worker_self_check.os, "environ", {TOLERATED_UNREAD_VARIABLE: value}
    )

    def read() -> worker_self_check.Snapshot:
        named = read_sealed_snapshot(sealed_root).environment_keys
        return replace(
            read_staged_root_as_sealed_process(sealed_root), environment_keys=named
        )

    assert read().environment_keys == {TOLERATED_UNREAD_VARIABLE}
    events: list[str] = []
    boundary = replace(boundary_over(sealed_snapshot(), events), read_snapshot=read)

    status = worker.main(arguments, boundary)

    assert status == 0
    assert ("serve" in events) is (not arguments)
    assert "start refused" not in caplog.text
