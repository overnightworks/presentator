"""The worker judges the environment's names, one set for every start."""

import pytest

from presentator.host.worker_self_check import (
    KUBERNETES_ENVIRONMENT_KEYS,
    Predicate,
    first_failing_predicate,
)
from presentator.host.worker_settings import worker_settings_variables
from tests.presentator.host.support.sealed_snapshot import (
    sealed_snapshot,
    with_fields,
)

SEALED_KEYS = sealed_snapshot().environment_keys
OTHER_SERVICE_LINK = "REDIS_SERVICE_HOST"

KEYS = {
    "the sealed names": (SEALED_KEYS, None),
    "the kubelet names": (SEALED_KEYS | KUBERNETES_ENVIRONMENT_KEYS, None),
    "the platform's keys": (
        worker_settings_variables() | KUBERNETES_ENVIRONMENT_KEYS,
        None,
    ),
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
