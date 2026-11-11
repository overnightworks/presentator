"""Cells 10 and 11 of the k3d driver: the web beside the worker, then a build."""

import contextlib
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.k3d_world import (
    GENERATED_VALUE,
    WEB_LABELS,
    WEB_READY_AFTER,
    Fixture,
    World,
    apply_positions,
    command_positions,
    fixture_maker,
    issued_commands,
)


@pytest.fixture
def cluster_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_cluster")


@pytest.fixture
def lives_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_lives")


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    return fixture_maker(load_script, tmp_path)


@pytest.fixture
def driven(make_fixture: Callable[..., Fixture]) -> Fixture:
    """A run over the default fake world, driven to its end."""
    fixture = make_fixture()
    fixture.drive()
    return fixture


def test_cell_10_applies_the_secret_and_web_reads_the_control_then_starts_the_worker(
    driven: Fixture,
) -> None:
    world = driven.world
    (secret,) = apply_positions(world, "Secret")
    (web,) = apply_positions(world, "Deployment")
    worker = apply_positions(world, "StatefulSet")[-1]
    (logged, *_) = command_positions(world, "kubectl", "logs", "--selector")
    reads = command_positions(world, "kubectl", "get", "pods")
    control = min(at for at in reads if at > logged)
    assert secret < web < logged < control < worker < max(reads)
    asked = issued_commands(world)[control][0]
    selector = asked[asked.index("--selector") + 1]
    assert dict(pair.split("=") for pair in selector.split(",")) == WEB_LABELS
    (container,) = world.applied("StatefulSet")[-1]["spec"]["template"]["spec"][
        "containers"
    ]
    assert "args" not in container


def test_cell_10_prints_its_control_before_the_worker_applies_then_its_ready_line(
    make_fixture: Callable[..., Fixture], monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = make_fixture()
    world = fixture.world
    said: list[tuple[int, str]] = []
    monkeypatch.setattr(
        fixture.driver, "say", lambda line: said.append((len(world.commands), line))
    )
    fixture.drive()
    worker = apply_positions(world, "StatefulSet")[-1]
    cell_ten = [(at, line) for at, line in said if line.startswith("cell 10: ")]
    ready = f"Ready {WEB_READY_AFTER} s after the worker started"
    worker_pod = "worker pod worker-uid, Running, restarts 0"
    control = (
        "cell 10: control: the web logged its wait, Ready False, "
        "container running, restarts 0"
    )
    served = f"cell 10: the web waited, then was {ready} (web restarts 0; {worker_pod})"
    assert [line for _, line in cell_ten] == [control, served]
    assert cell_ten[0][0] <= worker < cell_ten[1][0]


@pytest.mark.parametrize(
    "world",
    [World(web_logs_its_wait=False), World(web_never_ready=True)],
    ids=["no waiting line", "never Ready"],
)
def test_a_web_not_waiting_or_not_ready_fails_cell_10_within_the_window_with_logs(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    world: World,
) -> None:
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert "cell 10" in output.err
    assert world.web_log(previous=False) in output.out
    assert world.web_log(previous=True) in output.out
    assert world.issued("kubectl", "get", "events") != []
    polls = len(world.issued("kubectl", "get", "pods")) + len(
        world.issued("kubectl", "logs", "--selector")
    )
    # One window of polls, the control's read and the failure's two log prints.
    window = lives_module.TERMINATION_WINDOW_SECONDS // fixture.driver.POLL_SECONDS
    assert polls <= window + 1 + 3 + 1


@pytest.mark.parametrize(
    ("world", "fragment"),
    [
        (World(web_ready_early=True), "cell 10: the web was Ready before the worker"),
        (World(web_restarts=1), "restartCount is 1"),
        (World(remove_link_exit=1), "remove-link ended as 1"),
        (World(web_ready_after=-5), "Ready 5 s before the worker started"),
    ],
    ids=["the control", "a web restart", "remove-link", "Ready before the start"],
)
def test_a_web_or_worker_that_does_not_end_as_expected_fails_cell_10_naming_the_reason(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    world: World,
    fragment: str,
) -> None:
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert fragment in output.err
    assert "cell 10: FAILED" in output.out
    assert world.issued("kubectl", "get", "events") != []


@pytest.mark.parametrize(
    "world", [World(), World(web_never_ready=True)], ids=["a pass", "a failure"]
)
def test_a_generated_secret_value_reaches_only_the_secrets_apply_and_no_output(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    world: World,
) -> None:
    fixture = make_fixture(world)
    with contextlib.suppress(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert GENERATED_VALUE not in output.out + output.err
    (secret,) = apply_positions(world, "Secret")
    _, secret_stdin = issued_commands(world)[secret]
    values = json.loads(secret_stdin)["stringData"].values()
    assert values
    assert all(value.startswith(GENERATED_VALUE) for value in values)
    for command, _, stdin in world.commands:
        assert GENERATED_VALUE not in " ".join(command)
        assert (GENERATED_VALUE in (stdin or "")) == (stdin == secret_stdin)


def test_a_failing_secret_apply_refuses_without_the_value_its_stderr_echoes(
    make_fixture: Callable[..., Fixture],
    cluster_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()

    def echoing(
        _command: tuple[str, ...], _environment: object, stdin: str | None
    ) -> object:
        return cluster_module.Completed(1, "", stdin or "")

    effects = dataclasses.replace(fixture.run.effects, run=echoing)
    run = dataclasses.replace(fixture.run, effects=effects)
    secret = {"kind": "Secret", "stringData": {"key": f"{GENERATED_VALUE}x"}}
    with pytest.raises(SystemExit):
        fixture.driver.apply_secret(run, secret)
    err = capsys.readouterr().err
    assert "Secret" in err
    assert GENERATED_VALUE not in err
