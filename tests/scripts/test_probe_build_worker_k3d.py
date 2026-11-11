"""The k3d driver's decisions, driven through a fake runner that starts nothing."""

import copy
import json
import signal
import sys
from collections.abc import Callable, Generator, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

DIGEST = "@sha256:" + "a" * 64
REMOVE_LINK_IMAGE = "docker.io/library/busybox:1.38.0@sha256:" + "b" * 64
K3S_VERSION = "v1.37.2+k3s1"
RUN_IDENTIFIER = "0a1b2c3d"
OWN = "the run's own node"
EPOCH = datetime(2026, 11, 11, tzinfo=UTC)

Document = dict[str, Any]
Issued = tuple[tuple[str, ...], Mapping[str, str], str | None]


@pytest.fixture
def driver(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d")


@pytest.fixture
def production_image(driver: ModuleType) -> str:
    return driver.PRODUCTION_IMAGE + DIGEST


def workload_documents(image: str) -> list[Document]:
    """What the overlay and the base render, every production container at `image`."""

    def template(container: str, *, init: bool = False) -> Document:
        spec: Document = {"containers": [{"name": container, "image": image}]}
        if init:
            spec["initContainers"] = [
                {"name": "remove-link", "image": REMOVE_LINK_IMAGE}
            ]
        return {"spec": spec}

    def document(kind: str, name: str, **spec: Any) -> Document:  # noqa: ANN401
        return {"kind": kind, "metadata": {"name": name}, "spec": spec}

    job = {"spec": {"template": template("restore-test")}}
    return [
        document("Deployment", "web", replicas=1, template=template("web")),
        document("StatefulSet", "build-worker", template=template("worker", init=True)),
        document("CronJob", "restore-test", jobTemplate=job),
        document("PersistentVolumeClaim", "build-socket"),
        document("ServiceAccount", "build-worker"),
        document("ConfigMap", "sqlite"),
        document("Schedule", "backup"),
        document(
            "PodConfig",
            "backup",
            template={"spec": {"containers": [{"name": "backup"}]}},
        ),
    ]


def named(documents: list[Document], kind: str) -> Document:
    return next(document for document in documents if document["kind"] == kind)


def as_yaml_stream(documents: list[Document]) -> str:
    return "\n---\n".join(json.dumps(document) for document in documents)


def stamp(seconds: int) -> str:
    return (EPOCH + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def terminated_state(
    exit_code: int = REFUSAL_EXIT_STATUS,
    *,
    message: str = "",
    reason: str = "Error",
    started: int = 0,
) -> Document:
    return {
        "terminated": {
            "exitCode": exit_code,
            "reason": reason,
            "message": message,
            "startedAt": stamp(started),
            "finishedAt": stamp(started + 1),
        }
    }


@dataclass(frozen=True)
class Version:
    """What one version of a pod differs in."""

    init: bool = True
    init_exit: int | None = 0
    last_state: Document = field(default_factory=dict[str, Any])
    restarts: int = 0


PLAIN = Version()


def pod_version(
    uid: str,
    state: Document | None = None,
    version: Version = PLAIN,
) -> Document:
    """One version of a pod as kubectl's watch prints it."""
    worker: Document = {
        "name": "worker",
        "restartCount": version.restarts,
        "state": state or {"waiting": {}},
        "lastState": version.last_state,
    }
    status: Document = {"containerStatuses": [worker]}
    spec: Document = {"containers": [{"name": "worker"}]}
    if version.init:
        spec["initContainers"] = [{"name": "remove-link"}]
        init_state: Document = {"running": {}}
        if version.init_exit is not None:
            init_state = terminated_state(version.init_exit)
        status["initContainerStatuses"] = [{"name": "remove-link", "state": init_state}]
    return {"metadata": {"uid": uid}, "spec": spec, "status": status}


def watch_event(kind: str, pod: Document) -> Document:
    assert kind in {"ADDED", "MODIFIED", "DELETED"}, f"the fake does not know {kind}"
    return {"type": kind, "object": pod}


def life_events(
    uid: str,
    refusal: Predicate,
    *,
    started: int = 0,
    ended: Document | None = None,
    version: Version = PLAIN,
) -> list[Document]:
    """The events of one life that ends by a refusal: created, ended, deleted."""
    said = f"worker started\nstart refused: {refusal}\n"
    state = ended or terminated_state(message=said, started=started)
    pending = pod_version(uid, version=replace(version, init_exit=None))
    last = pod_version(uid, state, version)
    return [
        watch_event("ADDED", pending),
        watch_event("MODIFIED", last),
        watch_event("DELETED", last),
    ]


def lives_stream(
    count: int,
    refusal: Predicate,
    *,
    gap: int = 3,
    version: Version = PLAIN,
) -> list[Document]:
    """`count` lives, each worker starting `gap` seconds after its predecessor's end."""
    return [
        event
        for number in range(count)
        for event in life_events(
            f"life-{number}", refusal, started=number * (gap + 1), version=version
        )
    ]


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
    production_image: str = ""
    twin_pod: str = ""
    # The events the watch of each cell prints; empty is the passing pair.
    streams: list[list[Document]] = field(default_factory=list[list[Document]])
    # The watch ends in a signal's exit after its first event.
    interrupted: bool = False
    failing_command: tuple[str, ...] | None = None
    cell: int = 0
    overlay_file: str = ""
    commands: list[Issued] = field(default_factory=list[Issued])
    # Whether the run's directory existed as each command was issued.
    directory_present: dict[tuple[str, ...], bool] = field(
        default_factory=dict[tuple[str, ...], bool]
    )
    events_printed: int = 0
    watches_closed: int = 0

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
    ) -> tuple[int, str]:
        self.commands.append((command, environment, stdin))
        self.directory_present[command] = Path(
            environment["KUBECONFIG"]
        ).parent.exists()
        if (
            self.failing_command
            and command[: len(self.failing_command)] == self.failing_command
        ):
            message = "the fake runner broke"
            raise RuntimeError(message)
        answer = self.answer(command)
        return (1, "") if answer is None else (0, answer)

    def answer(self, command: tuple[str, ...]) -> str | None:
        match command:
            case ("kubectl", "kustomize", *_):
                return self.render(command)
            case ("kubectl", "get", "nodes", *_):
                return self.nodes_json()
            case ("kubectl", "get", "pod", name, *_) | ("kubectl", "logs", name, *_):
                if name != self.twin_pod:
                    message = f"the driver read {name}, which is gone with its life"
                    raise AssertionError(message)
                done = json.dumps({"status": {"phase": "Succeeded"}})
                return done if command[1] == "get" else ""
            case ("kubectl", "delete", "statefulset", *_):
                self.cell += 1
                return ""
            case _:
                return ""

    def watch(
        self, command: tuple[str, ...], environment: Mapping[str, str]
    ) -> Generator[str]:
        """The lines kubectl's watch prints; a closed stream is counted."""
        self.commands.append((command, environment, None))
        if not (
            command[0] == "timeout"
            and command[1].isdigit()
            and command[2:5] == ("kubectl", "get", "pods")
        ):
            message = f"the fake does not understand the watch {command}"
            raise AssertionError(message)
        return self.stream(self.streams[self.cell])

    def stream(self, events: list[Document]) -> Generator[str]:
        try:
            for event in events:
                self.events_printed += 1
                yield from (
                    f"{line}\n" for line in json.dumps(event, indent=4).splitlines()
                )
                if self.interrupted:
                    raise SystemExit(143)
        finally:
            self.watches_closed += 1

    def nodes_json(self) -> str:
        items = copy.deepcopy(self.nodes)
        for item in items:
            if item["metadata"]["name"] == OWN:
                item["metadata"]["name"] = self.node_name
        return json.dumps({"items": items})

    def render(self, command: tuple[str, ...]) -> str:
        if command[2].endswith("apps/presentator"):
            return as_yaml_stream(workload_documents(self.production_image))
        self.overlay_file = (Path(command[2]) / "kustomization.yaml").read_text()
        return as_yaml_stream(workload_documents(self.probe_image))

    def issued(self, *prefix: str) -> list[tuple[str, ...]]:
        return [
            command
            for command, _, _ in self.commands
            if command[: len(prefix)] == prefix
        ]

    def applies(self) -> list[list[Document]]:
        """The documents of each apply, in order."""
        return [
            list(YAML(typ="safe").load_all(stdin))  # pyright: ignore[reportUnknownMemberType]
            for command, _, stdin in self.commands
            if command[:2] == ("kubectl", "apply")
        ]

    def applied(self, kind: str) -> list[Document]:
        """The documents of the given kind that an apply received."""
        return [
            document
            for documents in self.applies()
            for document in documents
            if document["kind"] == kind
        ]


@dataclass
class Fixture:
    """A run over a fake world, with the driver that drives it."""

    driver: ModuleType
    world: World
    run: Any

    def drive(self) -> None:
        self.driver.drive(self.run)


@pytest.fixture
def platform(tmp_path: Path) -> Path:
    checkout = tmp_path / "platform"
    (checkout / "infrastructure" / "namespaces").mkdir(parents=True)
    (checkout / "mise.toml").write_text(f'[env]\nK3S_VERSION = "{K3S_VERSION}"\n')
    (checkout / "infrastructure" / "namespaces" / "namespaces.yaml").write_text(
        "kind: Namespace\nmetadata:\n  name: other\n---\n"
        "kind: Namespace\nmetadata:\n  name: presentator\n"
    )
    return checkout


@pytest.fixture
def make_fixture(
    driver: ModuleType, production_image: str, platform: Path, tmp_path: Path
) -> Callable[..., Fixture]:
    def make(
        world: World | None = None,
        *,
        head: str | None = None,
        missing: str | None = None,
    ) -> Fixture:
        world = world or World()
        directory = tmp_path / "run"
        ticks = [0.0]

        def sleep(seconds: float) -> None:
            ticks[0] += seconds

        def run_command(
            command: tuple[str, ...], environment: Mapping[str, str], stdin: str | None
        ) -> object:
            returncode, stdout = world(command, environment, stdin)
            return driver.Completed(returncode, stdout, "")

        def which(tool: str) -> str | None:
            return None if tool == missing else f"/bin/{tool}"

        def head_of(_checkout: Path) -> str:
            return head or driver.PLATFORM_COMMIT

        effects = driver.Effects(
            run=run_command,
            watch=world.watch,
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
        world.production_image, world.twin_pod = production_image, driver.TWIN_POD
        world.streams = world.streams or [
            lives_stream(1, Predicate.NO_ROUTE, version=Version(init=False)),
            lives_stream(driver.LIVES, Predicate.KUBERNETES_UNPROVEN),
        ]
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
    make_fixture: Callable[..., Fixture], capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = make_fixture()
    fixture.drive()
    lines = capsys.readouterr().out.splitlines()
    for cell, count, refusal in (
        (7, 1, Predicate.NO_ROUTE),
        (8, fixture.driver.LIVES, Predicate.KUBERNETES_UNPROVEN),
    ):
        lives = [line for line in lines if line.startswith(f"cell {cell}: life")]
        assert len(lives) == count
        assert all(f"start refused: {refusal}" in line for line in lives)
    intervals = [line for line in lines if line.startswith("cell 8: interval")]
    assert len(intervals) == fixture.driver.LIVES - 1
    assert all("3 s" in line and "resolution" in line for line in intervals)
    *_, (_, budget, *_rest) = fixture.world.issued("timeout")
    assert int(budget) >= (
        fixture.driver.TERMINATION_WINDOW_SECONDS
        + (fixture.driver.LIVES - 1) * CONNECT_WINDOW_SECONDS
    )


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


@pytest.mark.parametrize(
    ("cell", "init_container"), [(0, False), (1, True)], ids=["cell 7", "cell 8"]
)
def test_a_cell_applies_only_the_worker_pods_kinds_and_the_message_policy_differs(
    make_fixture: Callable[..., Fixture], *, cell: int, init_container: bool
) -> None:
    fixture = make_fixture()
    fixture.drive()
    rendered = workload_documents(fixture.world.probe_image)
    namespaces = fixture.world.applied("Namespace")
    assert [d["metadata"]["name"] for d in namespaces] == ["presentator"]
    cells = [
        documents
        for documents in fixture.world.applies()
        if documents[0]["kind"] != "Namespace"
    ]
    documents = cells[cell]
    assert {document["kind"] for document in documents} == {
        "PersistentVolumeClaim",
        "ServiceAccount",
        "StatefulSet",
    }
    spec = named(documents, "StatefulSet")["spec"]["template"]["spec"]
    (worker,) = spec["containers"]
    assert worker.pop("terminationMessagePolicy") == (
        fixture.driver.TERMINATION_MESSAGE_POLICY
    )
    expected = named(rendered, "StatefulSet")["spec"]["template"]["spec"]
    if not init_container:
        del expected["initContainers"]
    assert spec == expected


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
    assert fixture.world.directory_present[image[0]]
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


def judge_cell(
    make_fixture: Callable[..., Fixture],
    events: list[Document],
    refusal: Predicate,
    *,
    init_container: bool = True,
    lives: int = 1,
) -> tuple[str, World]:
    """Judge one cell over the watch's events; give its problem and the world."""
    world = World(streams=[events])
    fixture = make_fixture(world)
    expectation = fixture.driver.Expectation(
        refusal=refusal, init_container=init_container, lives=lives
    )
    documents = workload_documents(world.production_image)
    return fixture.driver.drive_cell(fixture.run, 8, documents, expectation), world


REFUSED = Predicate.KUBERNETES_UNPROVEN


refused_life = partial(life_events, "one", REFUSED)
GONE = terminated_state(137, reason="ContainerStatusUnknown")
SECOND = Version(last_state=terminated_state(started=9))
UNTERMINATED = [
    watch_event("ADDED", pod_version("one")),
    watch_event("DELETED", pod_version("one")),
]


@pytest.mark.parametrize(
    ("events", "fragment"),
    [
        (refused_life(ended=terminated_state(1)), "exit status 1"),
        (life_events("one", Predicate.ALONE), Predicate.ALONE),
        (refused_life(ended=terminated_state()), "start refused"),
        (refused_life(version=Version(init_exit=1)), "remove-link ended as 1"),
        (refused_life(version=Version(init=False)), "lacks an init container"),
        (refused_life(version=Version(restarts=1)), "restartCount is 1"),
        (refused_life(version=SECOND), "terminated 2 times"),
        (refused_life(ended=GONE), "ContainerStatusUnknown"),
        (UNTERMINATED, "deleted before its worker terminated"),
    ],
    ids=[
        "another exit status",
        "another predicate",
        "an empty message",
        "remove-link not ended 0",
        "the Pod has no init container",
        "a restart inside one uid",
        "a second termination inside one uid",
        "a lost sandbox",
        "deleted before the worker terminated",
    ],
)
def test_a_life_that_is_not_the_expected_one_fails_naming_its_uid_and_the_reason(
    make_fixture: Callable[..., Fixture], events: list[Document], fragment: str
) -> None:
    problem, _ = judge_cell(make_fixture, events, REFUSED)
    assert "uid one" in problem
    assert fragment in problem


@pytest.mark.parametrize(
    ("gap", "passes"),
    [
        (CONNECT_WINDOW_SECONDS - 1, True),
        (CONNECT_WINDOW_SECONDS, False),
        (CONNECT_WINDOW_SECONDS + 30, False),
    ],
    ids=["just below the window", "at the window", "above the window"],
)
def test_a_successor_must_start_below_the_connect_window(
    make_fixture: Callable[..., Fixture], *, gap: float, passes: bool
) -> None:
    events = lives_stream(2, REFUSED, gap=int(gap))
    problem, _ = judge_cell(make_fixture, events, REFUSED, lives=2)
    assert (problem == "") is passes
    assert passes or f"{gap:g} s" in problem


def test_a_watch_that_ends_early_fails_naming_the_lives_and_asks_for_the_diagnostics(
    make_fixture: Callable[..., Fixture],
) -> None:
    events = lives_stream(2, REFUSED)
    problem, world = judge_cell(make_fixture, events, REFUSED, lives=3)
    assert "2 of 3 lives" in problem
    assert "life-0" in problem
    assert "life-1" in problem
    assert world.issued("kubectl", "get", "events") != []
    assert world.applied("Pod") != []


def test_the_driver_stops_after_its_lives_and_closes_the_watch(
    make_fixture: Callable[..., Fixture],
) -> None:
    judged = lives_stream(2, REFUSED)
    later = life_events("later", REFUSED, ended=terminated_state(1))
    problem, world = judge_cell(make_fixture, judged + later, REFUSED, lives=2)
    assert problem == ""
    assert world.events_printed == len(judged)
    assert world.watches_closed == 1


def test_a_wrong_predicate_fails_the_cell_and_requests_the_events_and_the_twin_pod(
    driver: ModuleType,
    make_fixture: Callable[..., Fixture],
    capsys: pytest.CaptureFixture[str],
) -> None:
    world = World(
        streams=[
            life_events("one", Predicate.MOUNTS, version=Version(init=False)),
            lives_stream(driver.LIVES, REFUSED),
        ]
    )
    fixture = make_fixture(world)
    with pytest.raises(SystemExit):
        fixture.drive()
    output = capsys.readouterr()
    assert Predicate.MOUNTS in output.err
    assert "cell 7" in output.out
    assert world.issued("kubectl", "get", "events") != []
    twin = world.applied("Pod")[0]
    assert "/proc/self/mountinfo" in twin["spec"]["containers"][0]["command"][-1]
    assert "initContainers" not in twin["spec"]


def container_of(documents: list[Document], kind: str) -> Document:
    """The first container of the kind's pod template, a CronJob's job template too."""
    spec = named(documents, kind)["spec"]
    return spec.get("jobTemplate", {}).get("spec", spec)["template"]["spec"][
        "containers"
    ][0]


Change = Callable[[list[Document], list[Document]], object]
CHANGES: list[tuple[Change, str]] = [
    (lambda _b, o: named(o, "Deployment")["spec"].update(replicas=3), "replicas"),
    (
        lambda _b, o: named(o, "StatefulSet")["spec"]["template"]["spec"][
            "initContainers"
        ][0].update(image="busybox:latest"),
        "initContainers",
    ),
    (
        lambda b, o: container_of(o, "Deployment").update(
            image=container_of(b, "Deployment")["image"]
        ),
        "Deployment web",
    ),
    (
        lambda b, o: container_of(o, "CronJob").update(
            image=container_of(b, "CronJob")["image"]
        ),
        "CronJob restore-test",
    ),
    (
        lambda _b, o: container_of(o, "CronJob").update(
            image=container_of(o, "CronJob")["image"] + DIGEST
        ),
        "digest",
    ),
    (
        lambda b, _o: container_of(b, "StatefulSet").update(image=REMOVE_LINK_IMAGE),
        "worker",
    ),
]


@pytest.mark.parametrize(
    ("change", "named_in_refusal"),
    CHANGES,
    ids=[
        "another field changed",
        "remove-link's digest changed",
        "the web left at the production image",
        "the CronJob's container left at the production image",
        "a digest on the probe image",
        "the worker container not swapped",
    ],
)
def test_the_overlay_render_is_refused_for_each_difference_beyond_the_image_fields(
    driver: ModuleType,
    production_image: str,
    change: Change,
    named_in_refusal: str,
) -> None:
    probe_image = f"{driver.PROBE_REPOSITORY}:{RUN_IDENTIFIER}"
    base = workload_documents(production_image)
    overlay = workload_documents(probe_image)
    assert driver.render_problem(base, overlay, probe_image) is None
    change(base, overlay)
    assert named_in_refusal in driver.render_problem(base, overlay, probe_image)


def test_the_watch_hands_out_an_event_before_the_stream_goes_on(
    driver: ModuleType,
) -> None:
    first = watch_event("ADDED", pod_version("one"))
    second = watch_event("DELETED", pod_version("one"))

    def lines() -> Iterator[str]:
        yield from f"{json.dumps(first, indent=4)}\n".splitlines(keepends=True)
        yield json.dumps(second)
        message = "the stream was read on before the first event was handed out"
        raise AssertionError(message)

    events = driver.watch_events(lines())
    assert next(events) == first
    assert next(events) == second


def test_a_watch_that_ends_inside_an_event_is_refused(driver: ModuleType) -> None:
    with pytest.raises(SystemExit):
        list(driver.watch_events(['{"type": "ADD']))


@pytest.mark.parametrize("stop", ["kill_child", "close"])
def test_stopping_the_stream_kills_and_reaps_its_child(
    driver: ModuleType, stop: str
) -> None:
    runner = driver.SubprocessRunner()
    program = "import time; print('ready', flush=True); time.sleep(60)"
    lines = runner.stream((sys.executable, "-c", program), {})
    assert next(lines) == "ready\n"
    child = runner.child
    getattr(runner if stop == "kill_child" else lines, stop)()
    assert child.returncode == -signal.SIGKILL
    lines.close()
    assert runner.child is None


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
