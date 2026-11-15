"""Cells 10 and 11 of the k3d driver: the web beside the worker, then a build."""

import contextlib
import dataclasses
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.k3d_world import (
    FIRST_WORKER_UID,
    GENERATED_VALUE,
    NEXT_WORKER_UID,
    SCRATCH_MOUNT,
    WEB_LABELS,
    WEB_POD,
    WEB_READY_AFTER,
    Fixture,
    World,
    apply_positions,
    client_output,
    command_positions,
    delivered_outcome,
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
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture()
    world = fixture.world
    marker = "command issued at "
    run_command = fixture.run.effects.run

    def marking(
        command: tuple[str, ...], environment: Mapping[str, str], stdin: str | None
    ) -> object:
        fixture.driver.say(f"{marker}{len(world.commands)}")
        return run_command(command, environment, stdin)

    effects = dataclasses.replace(fixture.run.effects, run=marking)
    fixture.run = dataclasses.replace(fixture.run, effects=effects)
    fixture.drive()
    worker = apply_positions(world, "StatefulSet")[-1]
    printed = capsys.readouterr().out.splitlines()
    issued_before = 0
    cell_ten: list[tuple[int, str]] = []
    for line in printed:
        if line.startswith(marker):
            issued_before = int(line.removeprefix(marker)) + 1
        elif line.startswith("cell 10: "):
            cell_ten.append((issued_before, line))
    ready = f"Ready {WEB_READY_AFTER} s after the worker started"
    worker_pod = f"worker pod {FIRST_WORKER_UID}, Running, restarts 0"
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
    window = lives_module.TERMINATION_WINDOW_SECONDS // lives_module.POLL_SECONDS
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


@pytest.fixture
def web_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_web")


def positions(world: World, verb: str) -> list[int]:
    """The positions of the bounded `kubectl <verb>` commands the run issued."""
    return [
        at
        for at, (command, _) in enumerate(issued_commands(world))
        if command[2:4] == ("kubectl", verb)
    ]


def cell_eleven(output: str) -> list[str]:
    return [line for line in output.splitlines() if line.startswith("cell 11: ")]


def test_the_shared_parse_reads_the_outcome_the_fake_client_prints(
    web_module: ModuleType,
) -> None:
    done = delivered_outcome(web_module)
    assert web_module.outcome_in(client_output(done)) == done


def test_cell_11_copies_the_deck_then_runs_the_web_client_and_prints_one_line(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    world = fixture.world
    commands = issued_commands(world)
    (copied,) = positions(world, "cp")
    (ran,) = positions(world, "exec")
    assert copied < ran
    deck = fixture.run.repository / "examples" / "hello-deck"
    assert str(deck) in commands[copied][0]
    assert f"{WEB_POD}:{SCRATCH_MOUNT}/{deck.name}" in commands[copied][0]
    program = web_module.CLIENT_PROGRAM.read_text(encoding="utf-8")
    assert commands[ran][1] == program
    arguments = (SCRATCH_MOUNT, *web_module.HELLO_STEPS)
    assert commands[ran][0][-len(arguments) :] == arguments
    done = delivered_outcome(web_module)
    listing = ", ".join(done.out)
    began = f"the steps began in worker pod {FIRST_WORKER_UID}"
    ran_on = f"the next pod {NEXT_WORKER_UID} runs with remove-link 0, restarts 0"
    delivered = f"(talk index.html, PDF %PDF-, out: {listing})"
    line = f"cell 11: hello-deck built and exported through the web {delivered}"
    assert cell_eleven(capsys.readouterr().out) == [f"{line}; {began}, {ran_on}"]


def test_cell_11_runs_nothing_in_the_web_when_cell_10_failed(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture(World(web_never_ready=True))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert positions(fixture.world, "cp") == []
    assert positions(fixture.world, "exec") == []
    assert cell_eleven(capsys.readouterr().out) == []


@pytest.mark.parametrize(
    ("world", "complaint"),
    [
        (World(client_exit=1, client_stderr="a traceback"), "a traceback"),
        (World(client_stdout="not json\n", client_stderr="a log"), "a log"),
        (World(copy_exit=1, copy_stderr="a tar error"), "a tar error"),
    ],
    ids=["client ended non-zero", "last line not JSON", "the copy failed"],
)
def test_a_deck_or_client_that_does_not_end_with_an_outcome_fails_cell_11_with_stderr(
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
    world: World,
    complaint: str,
) -> None:
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert "cell 11" in output.err
    assert complaint in output.out
    assert world.web_log(previous=False) in output.out
    assert world.issued("kubectl", "get", "events") != []


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"steps": ()}, "steps=()"),
        ({"talk_page": False}, "talk_page=False"),
        ({"pdf": False}, "pdf=False"),
    ],
    ids=["no step ran", "no talk page", "no PDF"],
)
def test_an_outcome_that_is_not_hello_deck_delivered_fails_cell_11_naming_it(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    changes: dict[str, object],
    fragment: str,
) -> None:
    done = dataclasses.replace(delivered_outcome(web_module), **changes)
    fixture = make_fixture(World(client_stdout=client_output(done)))
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert fragment in output.err
    assert "cell 11: FAILED" in output.out


def test_a_failed_step_fails_cell_11_naming_it(
    make_fixture: Callable[..., Fixture],
    web_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    done = delivered_outcome(web_module)
    failed = web_module.StepOutcome(
        step=done.steps[0].step, failed=True, failure={"reason": "toolchain"}
    )
    outcome = dataclasses.replace(done, steps=(failed, *done.steps[1:]))
    fixture = make_fixture(World(client_stdout=client_output(outcome)))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert "toolchain" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("world", "fragment"),
    [
        (World(worker_keeps_uid=True), f"no worker pod other than {FIRST_WORKER_UID}"),
        (World(next_remove_link_exit=1), "remove-link ended as 1"),
        (World(next_restarts=1), "restartCount is 1"),
    ],
    ids=["the uid is kept", "remove-link", "a restart"],
)
def test_a_next_worker_pod_that_is_not_new_or_not_clean_fails_cell_11_in_the_window(
    make_fixture: Callable[..., Fixture],
    lives_module: ModuleType,
    capsys: pytest.CaptureFixture[str],
    world: World,
    fragment: str,
) -> None:
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    assert fragment in capsys.readouterr().err
    (ran,) = positions(world, "exec")
    reads = [
        at
        for at in command_positions(world, "kubectl", "get", "pod")
        if at > ran and lives_module.WORKER_POD in issued_commands(world)[at][0]
    ]
    window = lives_module.TERMINATION_WINDOW_SECONDS // lives_module.POLL_SECONDS
    assert len(reads) <= window + 1
