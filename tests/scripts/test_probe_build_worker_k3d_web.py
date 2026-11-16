"""Cells 10 to 13 of the k3d driver: the web without workers, then over two."""

import contextlib
import dataclasses
import json
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.scripts.support.k3d_pods import (
    at,
)
from tests.scripts.support.k3d_world import (
    GENERATED_VALUE,
    INBOUND_PORT,
    SCRATCH_MOUNT,
    WEB_POD_NAMES,
    WORKER_POD_NAMES,
    Fixture,
    World,
    apply_positions,
    command_positions,
    fixture_maker,
    issued_commands,
)
from tests.scripts.support.web_client_output import client_output, delivered_outcome


@pytest.fixture
def cluster_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_cluster")


@pytest.fixture
def web_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_web")


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    return fixture_maker(load_script, tmp_path)


def cell_lines(output: str, number: int) -> list[str]:
    return [line for line in output.splitlines() if line.startswith(f"cell {number}:")]


def bounded(world: World, verb: str) -> list[int]:
    """The positions of the bounded `kubectl <verb>` commands the run issued."""
    return [
        at
        for at, (command, _) in enumerate(issued_commands(world))
        if command[2:4] == ("kubectl", verb)
    ]


def execs(world: World) -> list[tuple[tuple[str, ...], str]]:
    """The commands that sent a program into a pod, with the program."""
    return [
        (command, stdin)
        for command, stdin in issued_commands(world)
        if command[2:4] == ("kubectl", "exec")
    ]


def delivered(web_module: ModuleType, second_step_at: float = 105) -> Any:  # noqa: ANN401 - the web probe's Outcome, loaded by name
    return delivered_outcome(
        web_module, first_step_at=at(100), second_step_at=at(second_step_at)
    )


def test_the_shared_parse_reads_the_outcome_the_fake_client_prints(
    web_module: ModuleType,
) -> None:
    done = delivered(web_module)
    assert web_module.outcome_in(client_output(done)) == done


def test_the_secrets_go_in_before_any_worker_and_the_web_applies_before_any_exec(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    world = fixture.world
    (secrets,) = apply_positions(world, "Secret")
    first_worker = apply_positions(world, "StatefulSet")[0]
    (web,) = apply_positions(world, "Deployment")
    assert secrets < first_worker < web < bounded(world, "exec")[0]


def test_cell_10_has_the_web_ready_with_no_worker_and_a_build_end_as_an_outage(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    world = fixture.world
    first_client, *_ = execs(world)
    program = web_module.CLIENT_PROGRAM.read_text(encoding="utf-8")
    assert first_client[1] == program
    assert first_client[0][-3:] == (SCRATCH_MOUNT, *web_module.HELLO_STEPS)
    worker_after_web = [
        at
        for at in apply_positions(world, "StatefulSet")
        if at > apply_positions(world, "Deployment")[0]
    ]
    assert worker_after_web[0] > bounded(world, "cp")[0]
    lines = cell_lines(capsys.readouterr().out, 10)
    assert [line.split(",")[0] for line in lines] == [
        "cell 10: the web is Ready with no worker pod and no Service",
        "cell 10: a build that finds no worker fails as an outage",
    ]


@pytest.mark.parametrize(
    ("world", "fragment"),
    [
        (World(web_never_ready=True), "the web was not Ready within"),
        (World(web_restarts=2), "restartCount is 2, expected 0"),
        (
            World(outage_stdout="delivered"),
            "the client's last line is no outcome",
        ),
        (World(client_exit=1, client_stderr="boom"), "the client ended 1: boom"),
        (World(copy_exit=1, copy_stderr="no pod"), "copying the deck"),
    ],
    ids=["not ready", "restarted", "not an outcome", "client ended", "copy ended"],
)
def test_a_web_or_a_build_that_is_not_the_outage_fails_cell_10_with_the_events(
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


def test_a_build_that_delivers_with_no_worker_is_not_an_outage(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = World(client_stdout=client_output(delivered(web_module)))
    world.outage_stdout = world.client_stdout
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    assert "did not end as an outage" in capsys.readouterr().err


def test_cell_11_scales_the_web_to_two_and_builds_in_each_after_applying_the_workers(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    world = fixture.world
    (scaled,) = command_positions(world, "kubectl", "scale", "deployment", "web")
    last_worker_apply = apply_positions(world, "StatefulSet")[-1]
    builds = [
        at
        for at in bounded(world, "exec")
        if issued_commands(world)[at][0][-2:] == ("build", "export")
    ]
    assert len(builds) == len(WEB_POD_NAMES) + 1
    assert last_worker_apply < scaled < builds[1]
    copies = bounded(world, "cp")
    assert len(copies) == len(WEB_POD_NAMES)
    (line,) = cell_lines(capsys.readouterr().out, 11)
    assert all(name in line for name in WEB_POD_NAMES)
    assert "through one Service" in line


def test_the_workers_apply_with_two_replicas_and_the_cells_before_with_one(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    replicas = [
        statefulset["spec"]["replicas"]
        for statefulset in fixture.world.applied("StatefulSet")
    ]
    assert replicas[:-1] == [1] * (len(replicas) - 1)
    assert replicas[-1] == len(WORKER_POD_NAMES)


@pytest.mark.parametrize(
    ("world", "fragment", "cell"),
    [
        (World(workers_never_ready=True), "were not Ready within", 11),
        (World(workers_keep_uids=True), "did not take both worker pods", 11),
        (World(client_exit=1, client_stderr="boom"), "cell 10", 10),
        (
            World(workers_log_only_the_first_build=True),
            "the workers wrote no new connection.ended line",
            11,
        ),
    ],
    ids=["workers not ready", "workers not replaced", "client ended", "stale line"],
)
def test_workers_that_are_not_ready_or_not_both_used_fail_cell_11(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    world: World,
    fragment: str,
    cell: int,
) -> None:
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert fragment in output.err
    assert f"cell {cell}: FAILED" in output.out


def drive_with_second_step(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    admitted_at: float,
    ready_at: float,
) -> Fixture:
    done = delivered(web_module, second_step_at=admitted_at)
    world = World(client_stdout=client_output(done), first_worker_ready_at=ready_at)
    return make_fixture(world)


@pytest.mark.parametrize(
    ("admitted_at", "ready_at"),
    [(105, 110), (125.2, 125.5)],
    ids=["while the first worker restarts", "in the second it is Ready in, before"],
)
def test_a_second_step_admitted_before_the_first_worker_was_ready_again_passes_cell_11(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    admitted_at: float,
    ready_at: float,
) -> None:
    drive_with_second_step(make_fixture, web_module, admitted_at, ready_at).drive()


@pytest.mark.parametrize(
    ("admitted_at", "ready_at"),
    [(125.8, 125.5), (110.5, 110), (160, 110)],
    ids=["in the second it is Ready in, after", "just after", "long after"],
)
def test_a_second_step_admitted_after_the_first_worker_was_ready_again_fails_cell_11(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    admitted_at: float,
    ready_at: float,
) -> None:
    fixture = drive_with_second_step(make_fixture, web_module, admitted_at, ready_at)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert "after the first worker was Ready again" in output.err
    assert "cell 11: FAILED" in output.out


@pytest.mark.parametrize(
    ("admitted", "lines", "problem"),
    [
        (None, ["100.0 False True "], "never admitted"),
        (100.0, ["99.0 False True "], "no sample"),
        (100.0, ["100.0 "], "were not one restarting"),
        (100.0, ["100.0 False True True "], "were not one restarting"),
        (100.0, ["100.0 False False "], "were not one restarting"),
        (100.0, ["100.0 True True "], "Ready again"),
    ],
    ids=[
        "no admission",
        "an exhausted stream",
        "an empty reading",
        "a three-pod reading",
        "two workers down",
        "both Ready",
    ],
)
def test_an_odd_or_missing_sample_at_the_admission_fails_the_restart_overlap(
    load_script: Callable[..., ModuleType],
    admitted: float | None,
    lines: list[str],
    problem: str,
) -> None:
    k3d_web = load_script("probe_build_worker_k3d_web")
    result = k3d_web.restart_overlap_problem(iter(lines), admitted)
    assert result is not None
    assert problem in result


def test_a_build_that_does_not_deliver_in_a_web_pod_fails_cell_11_naming_the_pod(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    done = delivered(web_module)
    failed = dataclasses.replace(done, pdf=False)
    world = World(client_stdout=client_output(failed))
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    assert f"{WEB_POD_NAMES[0]} did not deliver" in capsys.readouterr().err
    assert [
        command
        for command in world.issued("kubectl", "logs", "--selector")
        if command[3].startswith("app.kubernetes.io/name=build-worker")
    ]


def test_cell_12_runs_the_hello_program_in_a_web_pod_and_prints_one_line(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    (hello,) = [
        (command, stdin)
        for command, stdin in execs(fixture.world)
        if stdin == web_module.HELLO_PROGRAM.read_text(encoding="utf-8")
    ]
    assert hello[0][5] in WEB_POD_NAMES
    (line,) = cell_lines(capsys.readouterr().out, 12)
    assert "were refused" in line


@pytest.mark.parametrize(
    ("world", "fragment"),
    [
        (
            World(hello_stdout=json.dumps({"wrong key": True})),
            "the workers proved themselves to",
        ),
        (World(hello_spends_a_life=True), "spent a worker's life"),
        (World(hello_stdout="not json"), "printed no JSON line"),
    ],
    ids=["proved to a wrong key", "spent a life", "not json"],
)
def test_a_hello_that_is_not_refused_or_spends_a_life_fails_cell_12(
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
    assert "cell 12: FAILED" in output.out


def test_cell_13_runs_the_egress_probe_in_a_worker_pod_aimed_at_the_other_pods(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    (probe,) = [
        command
        for command, stdin in execs(fixture.world)
        if stdin == web_module.EGRESS_PROGRAM.read_text(encoding="utf-8")
    ]
    assert probe[5] in WORKER_POD_NAMES
    targets = probe[probe.index("-") + 1 :]
    assert len(set(targets)) == len(targets) > 1
    assert all(":" in target for target in targets)
    (line,) = cell_lines(capsys.readouterr().out, 13)
    assert "got EPERM" in line


def test_cell_13_connects_from_a_web_pod_to_a_port_a_worker_pod_listens_on(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    inbound = web_module.INBOUND_PROGRAM.read_text(encoding="utf-8")
    listening, inside, reaching = [
        command for command, stdin in execs(fixture.world) if stdin == inbound
    ]
    assert listening[5] in WORKER_POD_NAMES
    assert listening[-1] == "listen"
    assert inside[5] == listening[5]
    assert inside[-1] == f"127.0.0.1:{INBOUND_PORT}"
    assert reaching[5] in WEB_POD_NAMES
    assert reaching[-2] == "reach"
    host, _, port = reaching[-1].rpartition(":")
    assert host in {pod["status"]["podIP"] for pod in fixture.world.worker_pods()}
    assert port == str(INBOUND_PORT)
    (line,) = cell_lines(capsys.readouterr().out, 13)
    assert "ended in a timeout" in line


def test_cell_13_admits_every_worker_port_by_policy_only_while_the_web_pod_connects(
    make_fixture: Callable[..., Fixture], web_module: ModuleType
) -> None:
    fixture = make_fixture()
    fixture.drive()
    inbound = web_module.INBOUND_PROGRAM.read_text(encoding="utf-8")
    issued = issued_commands(fixture.world)
    (reaching,) = [
        at
        for at, (command, stdin) in enumerate(issued)
        if stdin == inbound
        and command[-1] != f"127.0.0.1:{INBOUND_PORT}"
        and command[-2] == "reach"
    ]
    (applying,) = [
        at
        for at in apply_positions(fixture.world, "NetworkPolicy")
        if "\n---\n" not in issued[at][1]
    ]
    (deleting,) = [
        at
        for at, (command, _) in enumerate(issued)
        if command[:3] == ("kubectl", "delete", "networkpolicy")
    ]
    assert applying < reaching < deleting
    policy = json.loads(issued[applying][1])
    assert policy["spec"]["policyTypes"] == ["Ingress"]
    (rule,) = policy["spec"]["ingress"]
    assert "ports" not in rule


def test_a_listener_that_does_not_answer_on_the_pods_loopback_fails_cell_13(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture(World(listener_loopback_end="timeout"))
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert "did not answer on its loopback" in output.err
    assert "cell 13: FAILED" in output.out


@pytest.mark.parametrize("end", ["connected", "ECONNREFUSED"])
def test_a_worker_port_that_does_not_drop_a_web_pods_connection_fails_cell_13(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    end: str,
) -> None:
    fixture = make_fixture(World(inbound_end=end))
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert f"ended {end}" in output.err
    assert "cell 13: FAILED" in output.out
    assert fixture.world.issued("kubectl", "delete", "networkpolicy") != []


@pytest.mark.parametrize(
    ("world", "fragment"),
    [
        (World(egress_loopback="ECONNREFUSED"), "to the loopback ended"),
        (World(egress_off_loopback="connected"), "off the loopback ended"),
        (World(egress_users=()), "printed no attempts of the build user"),
        (World(egress_users=("build",)), "printed no attempts of the wire user"),
        (
            World(egress_stdout=json.dumps({"build": {"loopback": "connected"}})),
            "build user's attempts lack",
        ),
        (
            World(egress_stdout=json.dumps({"build": "connected", "wire": {}})),
            "printed no attempts of the build user",
        ),
    ],
    ids=[
        "loopback refused",
        "an address reached",
        "no user",
        "no wire user",
        "no target tried",
        "attempts that are no object",
    ],
)
def test_a_user_that_leaves_the_loopback_or_a_probe_that_tried_less_fails_cell_13(
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
    assert "cell 13: FAILED" in output.out


@pytest.mark.parametrize("failed", [False, True], ids=["a pass", "a failure"])
def test_a_generated_secret_value_reaches_only_the_secrets_apply_and_no_output(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    *,
    failed: bool,
) -> None:
    world = World(web_never_ready=failed)
    fixture = make_fixture(world)
    with contextlib.suppress(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert GENERATED_VALUE not in output.out + output.err
    (secret,) = apply_positions(world, "Secret")
    _, secret_stdin = issued_commands(world)[secret]
    documents = [json.loads(part) for part in secret_stdin.split("\n---\n")]
    values = [v for document in documents for v in document["stringData"].values()]
    assert values
    assert all(value.startswith(GENERATED_VALUE) for value in values)
    for command, _, stdin in world.commands:
        assert GENERATED_VALUE not in " ".join(command)
        assert (GENERATED_VALUE in (stdin or "")) == (stdin == secret_stdin)


def test_a_failing_secret_apply_refuses_without_the_value_its_stderr_echoes(
    load_script: Callable[..., ModuleType],
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
    apply_secrets = load_script("probe_build_worker_k3d_web").apply_secrets
    with pytest.raises(SystemExit):
        apply_secrets(run, [secret])
    err = capsys.readouterr().err
    assert "Secret" in err
    assert GENERATED_VALUE not in err
