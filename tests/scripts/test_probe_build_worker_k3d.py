"""The k3d driver's runs, driven through a fake runner that starts nothing."""

import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import BUILD_IDENTITY, Predicate
from tests.scripts.support.k3d_world import (
    Document,
    Fixture,
    Version,
    World,
    expected_ends,
    fixture_maker,
    lives_stream,
    named,
    node,
    pod_version,
    terminated_state,
    twin_answer,
    workload_documents,
)


@pytest.fixture
def cluster_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_cluster")


@pytest.fixture
def lives_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_lives")


@pytest.fixture
def driver_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d")


@pytest.fixture
def starts_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_starts")


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
        (7, 1, f"exit {REFUSAL_EXIT_STATUS}, start refused: {Predicate.NO_ROUTE}"),
        (8, lives_module.LIVES, "exit 0, self-check passed"),
    ):
        lives = [line for line in lines if line.startswith(f"cell {cell}: life")]
        assert len(lives) == count
        for position, line in enumerate(lives, start=1):
            assert line.startswith(
                f"cell {cell}: life {position}, uid life-{position - 1}, "
            )
            assert line.endswith(ending)
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


def applied_twins(world: World) -> list[str]:
    """The names of the Pods an apply received, in order."""
    return [pod["metadata"]["name"] for pod in world.applied("Pod")]


def test_a_residue_twin_differs_from_the_applied_pod_template_only_in_how_it_starts(
    driver_module: ModuleType, starts_module: ModuleType
) -> None:
    documents = workload_documents("image")
    template = named(documents, "StatefulSet")["spec"]["template"]["spec"]
    template["containers"][0]["args"] = ["--argument"]
    uid, gid = BUILD_IDENTITY
    twin = driver_module.residue_twin(documents, "work-entry", ["--tail"])
    assert twin["metadata"] == {
        "name": driver_module.residue_pod_name("work-entry"),
        "namespace": "presentator",
    }
    (worker,) = twin["spec"].pop("containers")
    (original,) = template["containers"]
    assert worker.pop("command") == [
        *("python", "-I", "-c", starts_module.RESIDUE_SOURCE.read_text()),
        *("work-entry", f"{uid}:{gid}"),
        *original["command"],
        *original["args"],
        "--tail",
    ]
    assert twin["spec"].pop("restartPolicy") == "Never"
    assert worker == {k: v for k, v in original.items() if k not in {"command", "args"}}
    assert twin["spec"] == {k: v for k, v in template.items() if k != "containers"}


def test_the_residue_twins_start_one_after_another_each_judged_by_its_own_predicate(
    make_fixture: Callable[..., Fixture],
    starts_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    driver, world = fixture.driver, fixture.world
    ends = expected_ends(driver.CONTROL, starts_module)
    names = [driver.residue_pod_name(residue) for residue, _ in ends]
    assert applied_twins(world) == [driver.TWIN_POD, *names]
    issued = [(command, stdin or "") for command, _, stdin in world.commands]
    worker_applied = max(
        at
        for at, (command, stdin) in enumerate(issued)
        if command[:2] == ("kubectl", "apply") and "StatefulSet" in stdin
    )
    stopped = next(
        at
        for at, (command, _) in enumerate(issued)
        if at > worker_applied and command[:3] == ("kubectl", "delete", "statefulset")
    )
    gone = next(
        at
        for at, (command, _) in enumerate(issued)
        if at > stopped
        and command[:3] == ("kubectl", "wait", "--for=delete")
        and f"pod/{driver.WORKER_POD}" in command
    )
    twin_applies = [
        at
        for at, (command, stdin) in enumerate(issued)
        if command[:2] == ("kubectl", "apply")
        and any(
            f'"name": "{name}"' in stdin or f"name: {name}\n" in stdin
            for name in [driver.TWIN_POD, *names]
        )
    ]
    assert len(twin_applies) == 1 + len(names)
    assert min(twin_applies) > gone
    tails = {
        pod["metadata"]["name"]: pod["spec"]["containers"][0]["command"][-1]
        for pod in world.applied("Pod")[1:]
    }
    assert [name for name, tail in tails.items() if tail == driver.CHECK_ARGUMENT] == [
        names[0]
    ]
    (removal,) = [c for c in world.issued("kubectl", "delete", "pod") if names[0] in c]
    assert set(names) <= set(removal)
    lines = capsys.readouterr().out.splitlines()
    nine = [line for line in lines if line.startswith("cell 9: ")]
    expected = [
        f"cell 9: {residue}: exit {REFUSAL_EXIT_STATUS}, start refused: {refusal}"
        if refusal
        else f"cell 9: {residue}: exit 0, self-check passed"
        for residue, refusal in ends
    ]
    assert nine[1:] == expected
    assert nine[0].startswith("cell 9: the worker pod's facts:")
    assert lines[lines.index(nine[0]) + 1] == world.twin_log


def test_cell_9_runs_and_prints_the_facts_when_cell_8_fails(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = World(
        streams=[
            lives_stream(1, Predicate.NO_ROUTE, version=Version(init=False)),
            lives_stream(lives_module.LIVES, Predicate.MOUNTS),
        ]
    )
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert "cell 8" in output.err
    assert "cell 9: nothing: exit 0, self-check passed" in output.out
    assert "cell 9: the worker pod's facts:" in output.out
    assert set(world.residue_pods) <= set(applied_twins(world))


def a_failed_end(refusal: Predicate | None) -> Document:
    return twin_answer(refusal, terminated_state(1, message="plant failed: x"))


def an_unterminated_end(_refusal: Predicate | None) -> Document:
    pod = pod_version("twin-uid")
    pod["status"]["phase"] = "Failed"
    return pod


def another_predicate(_refusal: Predicate | None) -> Document:
    return twin_answer(Predicate.MOUNTS)


def a_restart(refusal: Predicate | None) -> Document:
    return twin_answer(refusal, version=Version(restarts=1))


def a_failed_remove_link(refusal: Predicate | None) -> Document:
    return twin_answer(refusal, version=Version(init_exit=1))


@pytest.mark.parametrize(
    ("end", "fragment"),
    [
        (another_predicate, "no 'start refused"),
        (a_failed_end, "exit status 1"),
        (a_restart, "restartCount is 1"),
        (a_failed_remove_link, "remove-link ended as 1"),
        (an_unterminated_end, "before its worker terminated"),
    ],
    ids=["another predicate", "a failed plant", "a restart", "remove-link", "no end"],
)
def test_a_residue_twin_that_does_not_end_as_expected_fails_the_cell_naming_it(
    make_fixture: Callable[..., Fixture],
    starts_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    end: Callable[[Predicate | None], Document],
    fragment: str,
) -> None:
    (_, refusal, (residue, *_)) = starts_module.REFUSALS[0]
    world = World(outcomes={residue: end(refusal)})
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert f"cell 9: {residue}: " in output.err
    assert fragment in output.err
    assert world.issued("kubectl", "get", "events") != []
    facts = fixture.driver.TWIN_POD
    assert applied_twins(world) == [facts, *world.residue_pods, facts]


def test_a_failed_plant_prints_the_end_of_its_message(
    make_fixture: Callable[..., Fixture],
    starts_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (_, refusal, (residue, *_)) = starts_module.REFUSALS[0]
    fixture = make_fixture(World(outcomes={residue: a_failed_end(refusal)}))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert f"cell 9: {residue}: message: plant failed: x" in capsys.readouterr().out


@pytest.mark.parametrize("refusal", [Predicate.SCRATCH, Predicate.ALONE])
def test_the_control_that_refuses_fails_the_cell_naming_the_control(
    make_fixture: Callable[..., Fixture],
    driver_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    refusal: Predicate,
) -> None:
    world = World(outcomes={driver_module.CONTROL: twin_answer(refusal)})
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    err = capsys.readouterr().err
    assert f"cell 9: {fixture.driver.CONTROL}: " in err
    assert f"exit status {REFUSAL_EXIT_STATUS}" in err


def test_a_twin_that_does_not_end_within_the_window_stops_cell_9_after_that_window(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    starts_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (_, _, (residue, *_)) = starts_module.REFUSALS[0]
    running = {"metadata": {"uid": "twin-uid"}, "status": {"phase": "Running"}}
    fixture = make_fixture(World(outcomes={residue: running}))
    with pytest.raises(SystemExit):
        fixture.drive()
    driver, world = fixture.driver, fixture.world
    assert "did not end" in capsys.readouterr().err
    stalled = driver.residue_pod_name(residue)
    assert applied_twins(world)[-2:] == [stalled, driver.TWIN_POD]
    polls = world.issued("kubectl", "get", "pod", stalled)
    assert (
        len(polls) <= lives_module.TERMINATION_WINDOW_SECONDS // driver.POLL_SECONDS + 1
    )
    removal = next(c for c in world.issued("kubectl", "delete", "pod") if stalled in c)
    assert [n for n in removal if n.startswith(driver.RESIDUE_POD_PREFIX)] == (
        applied_twins(world)[1:-1]
    )
