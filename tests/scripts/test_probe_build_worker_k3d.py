"""The k3d driver's runs, driven through a fake runner that starts nothing."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker_self_check import Predicate
from tests.scripts.support.k3d_world import (
    Document,
    Fixture,
    World,
    fixture_maker,
    node,
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


def test_drive_refuses_a_checkout_that_is_not_the_pin_before_any_command(
    make_fixture: Callable[..., Fixture],
    cluster_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture(head="f" * 40)
    with pytest.raises(SystemExit):
        fixture.drive()
    refusal = capsys.readouterr().err
    assert "f" * 40 in refusal
    assert cluster_module.PLATFORM_COMMIT in refusal
    assert fixture.world.commands == []
    assert not fixture.run.directory.exists()


@pytest.mark.parametrize("tool", ["k3d", "kubectl", "docker"])
def test_drive_refuses_a_missing_tool_by_name_before_any_command(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str], tool: str
) -> None:
    fixture = make_fixture(missing=tool)
    with pytest.raises(SystemExit):
        fixture.drive()
    assert tool in capsys.readouterr().err
    assert fixture.world.commands == []
    assert not fixture.run.directory.exists()


@pytest.mark.parametrize(
    ("nodes", "named_in_refusal"),
    [
        ([node(), node("k3d-other-agent-0")], "2 nodes"),
        ([node("k3d-someone-else-server-0")], "k3d-someone-else-server-0"),
        ([node(ready=False)], "not Ready"),
        ([node(version="v1.0.0+k3s1")], "v1.0.0+k3s1"),
    ],
    ids=["a second node", "another name", "a node not Ready", "another k3s version"],
)
def test_drive_refuses_a_cluster_that_is_not_the_runs_own_before_any_write(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    nodes: list[Document],
    named_in_refusal: str,
) -> None:
    fixture = make_fixture(World(nodes=nodes))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert named_in_refusal in capsys.readouterr().err
    assert fixture.world.issued("kubectl", "apply") == []


def test_every_command_of_a_run_carries_the_runs_own_environment(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    directory = fixture.run.directory
    for command, environment, _ in fixture.world.commands:
        assert environment["KUBECONFIG"] == str(directory / "kubeconfig"), command
        assert Path(environment["KUBECACHEDIR"]).is_relative_to(directory), command
        assert environment["KUBERC"] == "off", command
    assert fixture.world.issued("k3d", "cluster", "delete") != []
    assert fixture.world.issued("timeout") != []


def test_drive_passes_prints_every_life_and_interval_and_bounds_the_watch(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    lines = capsys.readouterr().out.splitlines()
    for cell, count, ending in (
        (7, 1, f"exit 2, start refused: {Predicate.NO_ROUTE}"),
        (8, lives_module.LIVES, "exit 0, self-check passed"),
    ):
        lives = [line for line in lines if line.startswith(f"cell {cell}: life")]
        assert len(lives) == count
        assert all(line.endswith(ending) for line in lives)
    intervals = [line for line in lines if line.startswith("cell 8: interval")]
    assert len(intervals) == lives_module.LIVES - 1
    assert all("3 s" in line and "resolution" in line for line in intervals)
    *_, (_, budget, *_rest) = fixture.world.issued("timeout")
    assert int(budget) >= (
        lives_module.TERMINATION_WINDOW_SECONDS
        + (lives_module.LIVES - 1) * CONNECT_WINDOW_SECONDS
    )


@pytest.mark.parametrize(
    "failing", [("kubectl", "apply"), ("k3d", "cluster", "delete")], ids=str
)
def test_a_failure_still_removes_the_cluster_then_the_image_then_the_directory(
    make_fixture: Callable[..., Fixture], failing: tuple[str, ...]
) -> None:
    fixture = make_fixture(World(failing_command=failing))
    with pytest.raises(RuntimeError):
        fixture.drive()
    delete, image = fixture.world.commands[-2:]
    assert delete[0] == ("k3d", "cluster", "delete", fixture.run.name)
    assert image[0] == ("docker", "image", "rm", "--force", fixture.run.tag)
    for _, environment, _ in (delete, image):
        assert environment["KUBECONFIG"] == str(fixture.run.kubeconfig)
        assert Path(environment["KUBECACHEDIR"]).is_relative_to(fixture.run.directory)
        assert environment["KUBERC"] == "off"
    assert fixture.world.directory_present_at_last_command
    assert not fixture.run.directory.exists()


def test_a_signal_during_the_watch_closes_the_watch_then_tears_down(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture(World(interrupted=True))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert fixture.world.watches_closed == 1
    assert fixture.world.issued("k3d", "cluster", "delete") != []
    assert not fixture.run.directory.exists()


@pytest.mark.parametrize("stop", ["kill_child", "close"])
def test_stopping_the_stream_kills_and_reaps_its_child(
    cluster_module: ModuleType, stop: str
) -> None:
    runner = cluster_module.SubprocessRunner()
    program = "import time; print('ready', flush=True); time.sleep(60)"
    lines = runner.stream((sys.executable, "-c", program), {})
    assert next(lines) == "ready\n"
    child = runner.child
    getattr(runner if stop == "kill_child" else lines, stop)()
    assert child.returncode < 0
    lines.close()
    assert runner.child is None


def test_the_runner_hands_the_run_environment_to_the_child(
    cluster_module: ModuleType,
) -> None:
    runner = cluster_module.SubprocessRunner()
    done = runner(
        (
            sys.executable,
            "-c",
            "import os, sys; print(os.environ['PROBE_VALUE'], sys.stdin.read())",
        ),
        {"PROBE_VALUE": "seen"},
        "input",
    )
    assert (done.returncode, done.stdout.strip()) == (0, "seen input")
