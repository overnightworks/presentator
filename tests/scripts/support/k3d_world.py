"""The k3d driver tests' shared world: the fake runner, its run and the builders."""

import copy
import json
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

from ruamel.yaml import YAML

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


def workload_documents(image: str) -> list[Document]:
    """What the overlay and the base render, every production container at `image`."""

    def template(container: str, *, init: bool = False) -> Document:
        main: Document = {"name": container, "image": image}
        if container == "worker":
            main["command"] = ["serve-the-build-worker", "--flag"]
        spec: Document = {"containers": [main]}
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


def ending(refusal: Predicate | None, *, started: int = 0) -> Document:
    """The terminated state of a worker that refuses or, with none, exits 0."""
    if refusal is None:
        return terminated_state(0, reason="Completed", started=started)
    said = f"worker started\nstart refused: {refusal}\n"
    return terminated_state(message=said, started=started)


def life_events(
    uid: str,
    refusal: Predicate | None,
    *,
    started: int = 0,
    ended: Document | None = None,
    version: Version = PLAIN,
) -> list[Document]:
    """The events of one life that ends by a refusal or, with none, by exit 0."""
    state = ended or ending(refusal, started=started)
    pending = pod_version(uid, version=replace(version, init_exit=None))
    last = pod_version(uid, state, version)
    return [
        watch_event("ADDED", pending),
        watch_event("MODIFIED", last),
        watch_event("DELETED", last),
    ]


def lives_stream(
    count: int,
    refusal: Predicate | None,
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


def twin_answer(
    refusal: Predicate | None, state: Document | None = None, version: Version = PLAIN
) -> Document:
    """The bare Pod kubectl prints once its worker ended as `state` or as expected."""
    ended = state or ending(refusal)
    pod = pod_version("twin-uid", ended, version)
    phase = "Succeeded" if ended["terminated"]["exitCode"] == 0 else "Failed"
    pod["status"]["phase"] = phase
    return pod


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
    twin_log: str = "the twin's mount list"
    # What a residue's twin Pod shows by the residue's name; the expected end if absent.
    outcomes: dict[str, Document] = field(default_factory=dict[str, Document])
    # The twin Pods of the residues by their names, set up with the driver.
    residue_pods: dict[str, Document] = field(default_factory=dict[str, Document])
    # The events the watch of each cell prints; empty is the passing pair.
    streams: list[list[Document]] = field(default_factory=list[list[Document]])
    # The watch ends in a signal's exit after its first event.
    interrupted: bool = False
    failing_command: tuple[str, ...] | None = None
    cell: int = 0
    overlay_file: str = ""
    commands: list[Issued] = field(default_factory=list[Issued])
    directory_present_at_last_command: bool = False
    events_printed: int = 0
    watches_closed: int = 0
    # The listings the run's cluster and image appear in while they exist.
    cluster_name: str = ""
    cluster_up: bool = False
    image_built: bool = False
    # What the listings leave out of the run's own: cluster, node, volume or tag.
    omitted: frozenset[str] = frozenset()
    # A container of another cluster that every container listing shows.
    stray_container: str = ""
    # How many commands had been issued each time the signals were held.
    holds: list[int] = field(default_factory=list[int])

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
    ) -> tuple[int, str]:
        self.commands.append((command, environment, stdin))
        kubeconfig = Path(environment["KUBECONFIG"])
        self.directory_present_at_last_command = kubeconfig.parent.exists()
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
            case ("kubectl", "get", "pod", name, *_):
                return json.dumps(self.pod_named(name))
            case ("kubectl", "logs", name, *_):
                self.pod_named(name)
                return self.twin_log
            case ("kubectl", "delete", "statefulset", *_):
                self.cell += 1
            case ("k3d", *_) | ("docker", *_):
                return self.container_answer(command)
            case _:
                pass
        return ""

    def container_answer(self, command: tuple[str, ...]) -> str:
        """What k3d and docker print: the listings follow the cluster and the image."""
        match command:
            case ("k3d", "cluster", "create" | "delete", *_):
                self.cluster_up = command[2] == "create"
            case ("docker", "build", *_) | ("docker", "image", "rm", *_):
                self.image_built = command[1] == "build"
            case ("k3d", "cluster", "list", *_):
                own = f"{self.cluster_name}  1/1  0/0  true"
                return self.listing("cluster", own, exists=self.cluster_up)
            case ("docker", "ps", *_):
                serverlb = f"k3d-{self.cluster_name}-serverlb"
                own = [serverlb, *([] if "node" in self.omitted else [self.node_name])]
                return self.listing(
                    "container",
                    *own,
                    exists=self.cluster_up,
                    stray=self.stray_container,
                )
            case ("docker", "volume", "ls", *_):
                own = f"k3d-{self.cluster_name}-images"
                return self.listing("volume", own, exists=self.cluster_up)
            case ("docker", "image", "ls", *_, repository):
                probe = repository == self.probe_image.split(":")[0]
                own = self.probe_image if probe else ""
                return self.listing("tag", own, exists=self.image_built)
            case _:
                pass
        return ""

    def listing(self, what: str, *own: str, exists: bool, stray: str = "") -> str:
        """The lines of a listing: the run's own while they exist, unless omitted."""
        shown = own if exists and what not in self.omitted else ()
        return "".join(f"{name}\n" for name in (*shown, stray) if name)

    def lister(self, words: str) -> str:
        """A listing run outside any drive: no environment, no record."""
        return self.answer(tuple(words.split())) or ""

    def hold_signals(self) -> None:
        self.holds.append(len(self.commands))

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

    def pod_named(self, name: str) -> Document:
        """The bare Pod of the facts twin or of a residue's twin; no other exists."""
        if name == self.twin_pod:
            return {"status": {"phase": "Succeeded"}}
        if name not in self.residue_pods:
            message = f"the driver read {name}, which is gone with its life"
            raise AssertionError(message)
        return copy.deepcopy(self.residue_pods[name])

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
        commands = (command for command, _, _ in self.commands)
        return [command for command in commands if command[: len(prefix)] == prefix]

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


def platform_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "platform"
    (checkout / "infrastructure" / "namespaces").mkdir(parents=True)
    (checkout / "mise.toml").write_text(f'[env]\nK3S_VERSION = "{K3S_VERSION}"\n')
    (checkout / "infrastructure" / "namespaces" / "namespaces.yaml").write_text(
        "kind: Namespace\nmetadata:\n  name: other\n---\n"
        "kind: Namespace\nmetadata:\n  name: presentator\n"
    )
    return checkout


def expected_ends(
    control: str, starts_module: ModuleType
) -> list[tuple[str, Predicate | None]]:
    """The control and each residue with the predicate its owner pairs it with."""
    refusals: list[tuple[str, Predicate | None]] = [
        (residue, predicate)
        for _, predicate, residues in starts_module.REFUSALS
        for residue in residues
    ]
    return [(control, None), *refusals]


def residue_pods(
    load_script: Callable[..., ModuleType],
    driver: ModuleType,
    outcomes: dict[str, Document],
) -> dict[str, Document]:
    """Each twin Pod by name as kubectl prints it: the chosen or the expected end."""
    starts_module = load_script("probe_build_worker_starts")
    return {
        driver.residue_pod_name(residue): outcomes.get(residue, twin_answer(refusal))
        for residue, refusal in expected_ends(driver.CONTROL, starts_module)
    }


def fixture_maker(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    driver = load_script("probe_build_worker_k3d")
    cluster_module = load_script("probe_build_worker_k3d_cluster")
    lives_module = load_script("probe_build_worker_k3d_lives")
    manifests_module = load_script("probe_build_worker_k3d_manifests")
    production_image = manifests_module.PRODUCTION_IMAGE + DIGEST

    def make(
        world: World | None = None,
        *,
        head: str | None = None,
        missing: str | None = None,
    ) -> Fixture:
        world = world or World()
        ticks = [0.0]

        def sleep(seconds: float) -> None:
            ticks[0] += seconds

        def run_command(
            command: tuple[str, ...], environment: Mapping[str, str], stdin: str | None
        ) -> object:
            returncode, stdout = world(command, environment, stdin)
            return cluster_module.Completed(returncode, stdout, "")

        def which(tool: str) -> str | None:
            return None if tool == missing else f"/bin/{tool}"

        def head_of(_checkout: Path) -> str:
            return head or cluster_module.PLATFORM_COMMIT

        effects = cluster_module.Effects(
            run=run_command,
            watch=world.watch,
            which=which,
            platform_head=head_of,
            free_port=lambda: 6550,
            monotonic=lambda: ticks[0],
            sleep=sleep,
            hold_signals=world.hold_signals,
        )
        run = cluster_module.Run(
            identifier=RUN_IDENTIFIER,
            directory=tmp_path / "run",
            repository=tmp_path / "presentator",
            platform=platform_checkout(tmp_path),
            effects=effects,
        )
        world.node_name, world.probe_image = run.node, run.tag
        world.cluster_name = run.name
        world.production_image, world.twin_pod = production_image, driver.TWIN_POD
        world.residue_pods = residue_pods(load_script, driver, world.outcomes)
        world.streams = world.streams or [
            lives_stream(1, Predicate.NO_ROUTE, version=Version(init=False)),
            lives_stream(lives_module.LIVES, None),
        ]
        return Fixture(driver, world, run)

    return make
