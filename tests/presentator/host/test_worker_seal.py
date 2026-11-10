"""The build worker reads its seal and stays closed under Kubernetes."""

from collections.abc import Mapping
from dataclasses import replace
from functools import partial

import pytest

from presentator.host import worker
from presentator.host.config import ConfigurationError, worker_settings_variables
from presentator.host.worker import Boundary
from presentator.host.worker_self_check import (
    KUBERNETES_ENVIRONMENT_KEYS,
    SEAL_VARIABLE,
    Predicate,
    Seal,
    Snapshot,
    first_failing_predicate,
    seal_from_environment,
)
from tests.presentator.host.support.sealed_snapshot import (
    sealed_snapshot,
    with_fields,
)
from tests.presentator.host.support.worker_boundary import boundary_over

SEALED_KEYS = sealed_snapshot().environment_keys
OTHER_SERVICE_LINK = "REDIS_SERVICE_HOST"
PLATFORM_KEYS = worker_settings_variables() | {SEAL_VARIABLE}


def snapshot_under(seal: Seal, keys: frozenset[str]) -> Snapshot:
    return replace(sealed_snapshot(), seal=seal, environment_keys=keys)


SEAL_AND_KEYS = {
    "an unset seal": (Seal.COMPOSE, SEALED_KEYS, None),
    "the compose seal naming its variable": (
        Seal.COMPOSE,
        SEALED_KEYS | {SEAL_VARIABLE},
        None,
    ),
    "kubernetes with the kubelet names": (
        Seal.KUBERNETES,
        SEALED_KEYS | KUBERNETES_ENVIRONMENT_KEYS,
        Predicate.KUBERNETES_UNPROVEN,
    ),
    "kubernetes without the kubelet names": (
        Seal.KUBERNETES,
        SEALED_KEYS,
        Predicate.KUBERNETES_UNPROVEN,
    ),
    "kubernetes with the platform's keys": (
        Seal.KUBERNETES,
        PLATFORM_KEYS | KUBERNETES_ENVIRONMENT_KEYS,
        Predicate.KUBERNETES_UNPROVEN,
    ),
    "kubernetes with another service's link": (
        Seal.KUBERNETES,
        SEALED_KEYS | {OTHER_SERVICE_LINK},
        Predicate.ENVIRONMENT,
    ),
}


@pytest.mark.parametrize(
    ("seal", "keys", "refused"), SEAL_AND_KEYS.values(), ids=SEAL_AND_KEYS.keys()
)
def test_the_seal_and_the_environment_keys_name_the_failing_predicate(
    seal: Seal, keys: frozenset[str], refused: Predicate | None
) -> None:
    assert first_failing_predicate(snapshot_under(seal, keys)) == refused


@pytest.mark.parametrize("name", sorted(KUBERNETES_ENVIRONMENT_KEYS))
def test_a_kubelet_name_under_the_compose_seal_fails_the_environment(
    name: str,
) -> None:
    snapshot = snapshot_under(Seal.COMPOSE, SEALED_KEYS | {name})

    assert first_failing_predicate(snapshot) == Predicate.ENVIRONMENT


@pytest.mark.parametrize(
    "predicate", [name for name in Predicate if name != Predicate.KUBERNETES_UNPROVEN]
)
def test_under_kubernetes_an_earlier_failing_predicate_is_named_first(
    predicate: Predicate,
) -> None:
    snapshot = with_fields(seal=Seal.KUBERNETES, unreadable=frozenset({predicate}))(
        sealed_snapshot()
    )

    assert first_failing_predicate(snapshot) == predicate


def boundary_with_environment(
    environment: Mapping[str, str], events: list[str]
) -> Boundary:
    return replace(
        boundary_over(sealed_snapshot(), events),
        read_seal=partial(seal_from_environment, environment),
    )


@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_an_unset_seal_starts_the_compose_worker(arguments: list[str]) -> None:
    events: list[str] = []
    boundary = boundary_with_environment({}, events)

    status = worker.main(arguments, boundary)

    assert status == 0
    assert ("serve" in events) is (not arguments)


@pytest.mark.parametrize("value", ["Kubernetes", "k8s", ""])
@pytest.mark.parametrize("arguments", [[], ["--check"]])
def test_a_refused_seal_refuses_the_start_before_any_read(
    caplog: pytest.LogCaptureFixture, value: str, arguments: list[str]
) -> None:
    events: list[str] = []
    boundary = boundary_with_environment({SEAL_VARIABLE: value}, events)

    status = worker.main(arguments, boundary)

    assert (status, events) == (worker.REFUSAL_EXIT_STATUS, ["prctl", "umask"])
    assert "start refused: settings" in caplog.text
    assert SEAL_VARIABLE in caplog.text


def test_a_refused_seal_is_logged_without_its_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    boundary = boundary_with_environment({SEAL_VARIABLE: "unique-seal-4711"}, [])

    worker.main([], boundary)

    assert "unique-seal-4711" not in caplog.text


def test_a_check_under_kubernetes_exits_two_naming_kubernetes_unproven(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events: list[str] = []
    boundary = boundary_with_environment({SEAL_VARIABLE: Seal.KUBERNETES}, events)

    status = worker.main(["--check"], boundary)

    assert status == worker.REFUSAL_EXIT_STATUS
    assert "start refused: kubernetes-unproven" in caplog.text
    assert "settings" not in events


@pytest.mark.parametrize(
    ("environment", "seal"),
    [
        ({}, Seal.COMPOSE),
        ({SEAL_VARIABLE: "compose"}, Seal.COMPOSE),
        ({SEAL_VARIABLE: "kubernetes"}, Seal.KUBERNETES),
    ],
)
def test_the_seal_variable_selects_its_seal(
    environment: Mapping[str, str], seal: Seal
) -> None:
    assert seal_from_environment(environment) is seal


@pytest.mark.parametrize("value", ["", "Kubernetes", "COMPOSE", "k8s"])
def test_any_other_seal_value_is_refused_naming_the_variable(value: str) -> None:
    with pytest.raises(ConfigurationError, match=SEAL_VARIABLE):
        seal_from_environment({SEAL_VARIABLE: value})
