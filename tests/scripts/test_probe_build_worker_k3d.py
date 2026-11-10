"""The k3d driver's decisions, driven through a fake runner that starts nothing."""

import copy
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

PRODUCTION_IMAGE = "ghcr.io/overnightworks/presentator@sha256:" + "a" * 64
REMOVE_LINK_IMAGE = "docker.io/library/busybox:1.38.0@sha256:" + "b" * 64
K3S_VERSION = "v1.37.2+k3s1"
RUN_IDENTIFIER = "0a1b2c3d"
OWN = "the run's own node"

Document = dict[str, Any]
Issued = tuple[tuple[str, ...], Mapping[str, str], str | None]


@pytest.fixture
def driver(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d")


def workload_documents(web_image: str, worker_image: str) -> list[Document]:
    return [
        {
            "kind": "Deployment",
            "metadata": {"name": "web"},
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {"containers": [{"name": "web", "image": web_image}]}
                },
            },
        },
        {
            "kind": "StatefulSet",
            "metadata": {"name": "build-worker"},
            "spec": {
                "replicas": 1,
                "template": {
                    "spec": {
                        "initContainers": [
                            {"name": "remove-link", "image": REMOVE_LINK_IMAGE}
                        ],
                        "containers": [{"name": "worker", "image": worker_image}],
                    }
                },
            },
        },
    ]


def as_yaml_stream(documents: list[Document]) -> str:
    return "\n---\n".join(json.dumps(document) for document in documents)


def terminated(exit_code: int) -> Document:
    return {"terminated": {"exitCode": exit_code}}


def pod(
    *,
    worker_exit: int = REFUSAL_EXIT_STATUS,
    remove_link_exit: int | None = None,
    restarted: bool = True,
) -> Document:
    worker: Document = {"name": "worker", "state": {}, "lastState": {}}
    worker["lastState" if restarted else "state"] = terminated(worker_exit)
    status: Document = {"containerStatuses": [worker]}
    spec: Document = {"containers": [{"name": "worker"}]}
    if remove_link_exit is not None:
        status["initContainerStatuses"] = [
            {"name": "remove-link", "state": terminated(remove_link_exit)}
        ]
        spec["initContainers"] = [{"name": "remove-link"}]
    return {"spec": spec, "status": status}


def node(
    name: str = OWN, *, ready: bool = True, version: str = K3S_VERSION
) -> Document:
    return {
        "metadata": {"name": name},
        "status": {
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
            "nodeInfo": {"kubeletVersion": version},
        },
    }


@dataclass
class World:
    """Answers every command of a run and remembers it, with its environment."""

    nodes: list[Document] = field(default_factory=lambda: [node()])
    node_name: str = ""
    probe_image: str = ""
    pods: list[Document] = field(
        default_factory=lambda: [pod(), pod(remove_link_exit=0, restarted=False)]
    )
    logs: list[str] = field(
        default_factory=lambda: [
            f"start refused: {Predicate.NO_ROUTE}",
            f"start refused: {Predicate.KUBERNETES_UNPROVEN}",
        ]
    )
    failing_command: tuple[str, ...] | None = None
    cell: int = 0
    overlay_file: str = ""
    commands: list[Issued] = field(default_factory=list[Issued])

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
    ) -> str:
        self.commands.append((command, environment, stdin))
        if (
            self.failing_command
            and command[: len(self.failing_command)] == self.failing_command
        ):
            message = "the fake runner broke"
            raise RuntimeError(message)
        return self.answer(command)

    def answer(self, command: tuple[str, ...]) -> str:
        match command:
            case ("kubectl", "kustomize", *_) | ("yq", *_):
                return self.render(command)
            case ("kubectl", "get", "nodes", *_):
                return self.nodes_json()
            case ("kubectl", "get", "pod", name, *_):
                return json.dumps(self.pod_named(name))
            case ("kubectl", "logs", "build-worker-0", *_):
                return self.logs[self.cell]
            case ("kubectl", "delete", "statefulset", *_):
                self.cell += 1
                return ""
            case _:
                return ""

    def pod_named(self, name: str) -> Document:
        if name == "build-worker-0":
            return self.pods[self.cell]
        return {"status": {"phase": "Succeeded"}}

    def nodes_json(self) -> str:
        items = copy.deepcopy(self.nodes)
        for item in items:
            if item["metadata"]["name"] == OWN:
                item["metadata"]["name"] = self.node_name
        return json.dumps({"items": items})

    def render(self, command: tuple[str, ...]) -> str:
        if command[0] == "yq":
            return as_yaml_stream(
                self.statefulset(with_init_container="del(" not in command[1])
            )
        if command[2].endswith("apps/presentator"):
            return as_yaml_stream(
                workload_documents(PRODUCTION_IMAGE, PRODUCTION_IMAGE)
            )
        self.overlay_file = (Path(command[2]) / "kustomization.yaml").read_text()
        return as_yaml_stream(workload_documents(self.probe_image, self.probe_image))

    def statefulset(self, *, with_init_container: bool) -> list[Document]:
        document = workload_documents(self.probe_image, self.probe_image)[1]
        if not with_init_container:
            del document["spec"]["template"]["spec"]["initContainers"]
        return [document]

    def issued(self, *prefix: str) -> list[tuple[str, ...]]:
        return [
            command
            for command, _, _ in self.commands
            if command[: len(prefix)] == prefix
        ]

    def applied(self, kind: str) -> list[Document]:
        """The documents of the given kind that an apply received, each one JSON."""
        documents = [
            json.loads(stdin or "")
            for command, _, stdin in self.commands
            if command[:2] == ("kubectl", "apply")
        ]
        return [document for document in documents if document["kind"] == kind]


@dataclass
class Fixture:
    """A run over a fake world, with the driver that drives it."""

    driver: ModuleType
    world: World
    run: Any

    def drive(self) -> None:
        self.driver.drive(self.run)


@pytest.fixture
def make_fixture(driver: ModuleType, tmp_path: Path) -> Callable[..., Fixture]:
    def make(
        world: World | None = None,
        *,
        head: str | None = None,
        missing: str | None = None,
    ) -> Fixture:
        world = world or World()
        platform = tmp_path / "platform"
        (platform / "infrastructure" / "namespaces").mkdir(parents=True)
        (platform / "mise.toml").write_text(f'[env]\nK3S_VERSION = "{K3S_VERSION}"\n')
        (platform / "infrastructure" / "namespaces" / "namespaces.yaml").write_text(
            "kind: Namespace\nmetadata:\n  name: other\n---\n"
            "kind: Namespace\nmetadata:\n  name: presentator\n"
        )
        directory = tmp_path / "run"
        ticks = [0.0]

        def sleep(seconds: float) -> None:
            ticks[0] += seconds

        def run_command(
            command: tuple[str, ...], environment: Mapping[str, str], stdin: str | None
        ) -> object:
            return driver.Completed(0, world(command, environment, stdin), "")

        def which(tool: str) -> str | None:
            return None if tool == missing else f"/bin/{tool}"

        def head_of(_checkout: Path) -> str:
            return head or driver.PLATFORM_COMMIT

        effects = driver.Effects(
            run=run_command,
            which=which,
            platform_head=head_of,
            free_port=lambda: 6550,
            monotonic=lambda: ticks[0],
            sleep=sleep,
        )
        run = driver.Run(
            identifier=RUN_IDENTIFIER,
            directory=directory,
            repository=tmp_path / "presentator",
            platform=platform,
            effects=effects,
        )
        world.node_name, world.probe_image = run.node, run.tag
        return Fixture(driver, world, run)

    return make


def test_drive_refuses_a_checkout_that_is_not_the_pin_before_any_command(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture(head="f" * 40)
    with pytest.raises(SystemExit):
        fixture.drive()
    refusal = capsys.readouterr().err
    assert "f" * 40 in refusal
    assert fixture.driver.PLATFORM_COMMIT in refusal
    assert fixture.world.commands == []
    assert not fixture.run.directory.exists()


@pytest.mark.parametrize("tool", ["k3d", "kubectl", "yq"])
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
    ("nodes", "named"),
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
    named: str,
) -> None:
    fixture = make_fixture(World(nodes=nodes))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert named in capsys.readouterr().err
    assert fixture.world.issued("kubectl", "apply") == []


def test_every_command_of_a_run_carries_the_runs_kubeconfig_including_the_delete(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    kubeconfigs = {env["KUBECONFIG"] for _, env, _ in fixture.world.commands}
    assert kubeconfigs == {str(fixture.run.directory / "kubeconfig")}
    assert fixture.world.issued("k3d", "cluster", "delete") != []


def test_drive_passes_when_the_control_and_the_twin_refuse_as_expected(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture()
    fixture.drive()
    assert fixture.world.issued("kubectl", "apply", "--filename", "-") != []
    output = capsys.readouterr().out
    for cell, log in zip((7, 8), fixture.world.logs, strict=True):
        assert f"cell {cell}: exit {REFUSAL_EXIT_STATUS}, {log}" in output


def test_the_overlay_names_the_platform_app_by_a_path_relative_to_itself(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    (resource,) = json.loads(fixture.world.overlay_file)["resources"]
    overlay = fixture.run.directory / "overlay"
    assert not Path(resource).is_absolute()
    assert (overlay / resource).resolve() == (
        fixture.run.platform / "apps" / "presentator"
    ).resolve()


def test_drive_applies_only_the_presentator_namespace_document(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture()
    fixture.drive()
    namespaces = fixture.world.applied("Namespace")
    assert [document["metadata"]["name"] for document in namespaces] == ["presentator"]


def test_drive_removes_the_cluster_and_the_image_after_a_failure_mid_run(
    make_fixture: Callable[..., Fixture],
) -> None:
    fixture = make_fixture(World(failing_command=("kubectl", "apply")))
    with pytest.raises(RuntimeError):
        fixture.drive()
    (delete,) = fixture.world.issued("k3d", "cluster", "delete")
    assert delete[-1] == fixture.run.name
    assert fixture.world.issued("docker", "image", "rm") == [
        ("docker", "image", "rm", "--force", fixture.run.tag)
    ]
    assert not fixture.run.directory.exists()


def test_a_wrong_predicate_fails_the_cell_and_requests_the_events_and_the_twin_pod(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    world = World(
        logs=[
            f"start refused: {Predicate.MOUNTS}",
            f"start refused: {Predicate.KUBERNETES_UNPROVEN}",
        ]
    )
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert Predicate.MOUNTS in output.err
    assert "cell 8" in output.out
    assert world.issued("kubectl", "get", "events") != []
    twin = world.applied("Pod")[0]
    assert "/proc/self/mountinfo" in twin["spec"]["containers"][0]["command"][-1]
    assert "initContainers" not in twin["spec"]


def test_a_cell_without_termination_in_the_window_fails_naming_the_wait(
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    waiting: Document = {
        "spec": {"containers": []},
        "status": {"containerStatuses": [{"name": "worker", "state": {"waiting": {}}}]},
    }
    fixture = make_fixture(World(pods=[waiting, waiting]))
    with pytest.raises(SystemExit):
        fixture.drive()
    assert (
        f"no termination within {fixture.driver.TERMINATION_WINDOW_SECONDS} s"
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("pod_document", "log", "named"),
    [
        (pod(worker_exit=1), f"start refused: {Predicate.NO_ROUTE}", "exit status 1"),
        (pod(), f"start refused: {Predicate.ALONE}", Predicate.ALONE),
        (
            pod(remove_link_exit=0),
            f"start refused: {Predicate.NO_ROUTE}",
            "init container",
        ),
    ],
    ids=[
        "another exit status",
        "another predicate",
        "an init container on the control",
    ],
)
def test_the_control_fails_for_each_deviation_and_names_it(
    driver: ModuleType, pod_document: Document, log: str, named: str
) -> None:
    expectation = driver.Expectation(refusal=Predicate.NO_ROUTE, init_container=False)
    assert named in driver.worker_problem(pod_document, log, expectation)


def test_the_twin_fails_when_remove_link_does_not_end_zero(driver: ModuleType) -> None:
    expectation = driver.Expectation(
        refusal=Predicate.KUBERNETES_UNPROVEN, init_container=True
    )
    failed_init = pod(remove_link_exit=1)
    assert "remove-link" in driver.worker_problem(
        failed_init, f"start refused: {Predicate.KUBERNETES_UNPROVEN}", expectation
    )
    assert (
        driver.worker_problem(
            pod(remove_link_exit=0),
            f"start refused: {Predicate.KUBERNETES_UNPROVEN}",
            expectation,
        )
        is None
    )


def change_replicas(documents: list[Document]) -> None:
    documents[0]["spec"]["replicas"] = 3


def change_remove_link_image(documents: list[Document]) -> None:
    init = documents[1]["spec"]["template"]["spec"]["initContainers"][0]
    init["image"] = "busybox:latest"


def leave_web_at_ghcr(documents: list[Document]) -> None:
    documents[0]["spec"]["template"]["spec"]["containers"][0]["image"] = (
        PRODUCTION_IMAGE
    )


@pytest.mark.parametrize(
    ("change", "named"),
    [
        (change_replicas, "replicas"),
        (change_remove_link_image, "initContainers"),
        (leave_web_at_ghcr, "left at"),
    ],
    ids=[
        "another field changed",
        "remove-link's digest changed",
        "an image left at ghcr.io",
    ],
)
def test_the_overlay_render_is_refused_for_each_difference_beyond_the_two_images(
    driver: ModuleType, change: Callable[[list[Document]], object], named: str
) -> None:
    probe_image = f"{driver.PROBE_REPOSITORY}:{RUN_IDENTIFIER}"
    base = workload_documents(PRODUCTION_IMAGE, PRODUCTION_IMAGE)
    overlay = workload_documents(probe_image, probe_image)
    assert driver.render_problem(base, overlay, probe_image) is None
    change(overlay)
    assert named in driver.render_problem(base, overlay, probe_image)


def test_a_digest_on_the_probe_image_is_refused(driver: ModuleType) -> None:
    base = workload_documents(PRODUCTION_IMAGE, PRODUCTION_IMAGE)
    probe_image = f"{driver.PROBE_REPOSITORY}:{RUN_IDENTIFIER}"
    overlay = workload_documents(probe_image + "@sha256:" + "c" * 64, probe_image)
    assert "digest" in driver.render_problem(base, overlay, probe_image)


def test_the_runner_hands_the_run_environment_to_the_child(driver: ModuleType) -> None:
    runner = driver.SubprocessRunner()
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
