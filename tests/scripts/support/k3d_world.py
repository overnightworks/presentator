"""The k3d driver tests' shared world: the fake runner, its run and the builders."""

import copy
import itertools
import json
import signal
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from logline import Outcome
from ruamel.yaml import YAML

from presentator.host.worker_self_check import Predicate
from presentator.host.worker_serving import CONNECTION_EVENT
from presentator.host.worker_wire import SERVICE_NAME
from tests.scripts.support.k3d_pods import (
    K3S_VERSION,
    OWN,
    Document,
    Version,
    lives_stream,
    node,
    stamp,
    twin_answer,
)
from tests.scripts.support.k3d_render import as_yaml_stream, workload_documents
from tests.scripts.support.web_client_output import (
    client_output,
    delivered_outcome,
    outage_outcome,
)

DIGEST = "@sha256:" + "a" * 64
OTHER_DIGEST = "@sha256:" + "c" * 64
RELEASED_VERSION = "2026.11.11.1"
RUN_IDENTIFIER = "0a1b2c3d"
GENERATED_VALUE = "generated-value-"
WEB_POD_NAMES = ("web-pod-a", "web-pod-b")
WORKER_POD_NAMES = ("build-worker-0", "build-worker-1")
INBOUND_PORT = 40123
# How long after its creation a fake pod turns Ready.
READY_AFTER_SECONDS = 5

Issued = tuple[tuple[str, ...], Mapping[str, str], str | None]


def kubelet_config(**changes: object) -> Document:
    """The node's configz, which keeps every image, with the changes to its fields."""
    kubelet: Document = {
        "imageGCHighThresholdPercent": 100,
        "imageMaximumGCAge": "0s",
        "evictionHard": {
            "imagefs.available": "0%",
            "nodefs.available": "0%",
            "memory.available": "100Mi",
        },
    }
    return {"kubeletconfig": kubelet | changes}


@dataclass
class World:
    """Answers every command of a run and remembers it, with its environment."""

    nodes: list[Document] = field(default_factory=lambda: [node()])
    node_name: str = ""
    kubelet: Document = field(default_factory=kubelet_config)
    # What the dump of a failed cell reads: the node's events and images as the
    # kubelet and as containerd list them, the runner's disk use.
    node_events: str = "a node event"
    node_images: str = "a node image"
    node_containerd_images: str = "a containerd image"
    disk_use: str = "a disk use"
    probe_image: str = ""
    # The local names of the probe's throwaway stores, Redis first.
    store_images: tuple[str, ...] = ()
    production_image: str = ""
    # The repo digests the probe tag shows once the published image is tagged;
    # the published image's own if unset.
    repo_digests: list[str] | None = None
    # The error `docker pull` ends with; the pull passes if empty.
    pull_error: str = ""
    # The store whose image `k3d image import` and the node's runtime fail on, by
    # its place in store_images: the import's error (passes if empty), how many
    # of its imports exit 0 yet leave the node without it, and how many looks
    # into the node find it once an import kept it (every look if None, at
    # least one: an import the node never holds is a lost import).
    failing_store: int = 0
    store_import_error: str = ""
    store_imports_lost: int = 0
    store_found_in_node: int | None = None
    # The lines every `k3d image import` that exits 0 writes, in order.
    import_log: tuple[str, ...] = (
        "INFO[0000] Importing image(s) into cluster\n",
        "INFO[0001] Successfully imported 1 image(s) into 1 cluster(s)\n",
    )
    twin_pod: str = ""
    twin_log: str = "the twin's mount list"
    # What the web and the workers do: whether they become Ready, the web's
    # restarts, and whether the hello program spends a worker's life.
    web_never_ready: bool = False
    web_restarts: int = 0
    workers_never_ready: bool = False
    workers_keep_uids: bool = False
    # The workers write an ok line for the first build, and none for a later one.
    workers_log_only_the_first_build: bool = False
    # The scheduler binds the last replacement worker pod although the node is
    # cordoned; it is not Ready yet while the hold is read.
    hold_breaks: bool = False
    # The runner breaks on the first build the web runs over the workers.
    build_over_workers_breaks: bool = False
    hello_spends_a_life: bool = False
    # What the programs sent into the pods print and how the copy ends: the web's
    # client with no worker and with workers (set up with the driver if empty),
    # its end, the hello program and the egress probe.
    outage_stdout: str = ""
    client_stdout: str = ""
    client_exit: int = 0
    client_stderr: str = ""
    hello_stdout: str = ""
    egress_stdout: str = ""
    # How the egress probe's attempts end when egress_stdout is unset, and how a
    # web pod's connection to the worker pod's other port ends.
    egress_users: tuple[str, ...] = ("build", "wire")
    egress_loopback: str = "connected"
    egress_off_loopback: str = "EPERM"
    inbound_end: str = "timeout"
    # How many connections of the relabelled web pod to a worker's build port
    # connect before the cluster drops them, and how many time out after the
    # probe's policy is applied before it admits them; None when it never does.
    connects_before_the_drop: int | None = 1
    timeouts_before_the_admission: int | None = 1
    # How a connection to the listener ends from inside the worker pod itself.
    listener_loopback_end: str = "connected"
    copy_exit: int = 0
    copy_stderr: str = ""
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
    # The local names of the stores tagged and not yet removed.
    stores_present: set[str] = field(default_factory=set[str])
    # The images volume lives from the cluster's creation until it is removed;
    # `k3d cluster delete` cannot remove it while the import's tools container uses it.
    volume_up: bool = False
    tools_up: bool = False
    # A signal ends `k3d image import` after it started its tools container.
    import_interrupted: bool = False
    # What the listings leave out of the run's own: cluster, node, volume or tag.
    omitted: frozenset[str] = frozenset()
    # A container of another cluster that every container listing shows.
    stray_container: str = ""
    # How many commands had been issued each time the signals were held.
    holds: list[int] = field(default_factory=list[int])

    def __post_init__(self) -> None:
        """Refuse a second knob for a lost import."""
        if self.store_found_in_node == 0:
            message = "a node that never holds the image is store_imports_lost"
            raise ValueError(message)

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
        stderr_into_stdout: bool,  # noqa: FBT001 - Effects.run's positional shape
    ) -> tuple[int, str, str]:
        self.commands.append((command, environment, stdin))
        kubeconfig = Path(environment["KUBECONFIG"])
        self.directory_present_at_last_command = kubeconfig.parent.exists()
        if (
            self.failing_command
            and command[: len(self.failing_command)] == self.failing_command
        ):
            message = "the fake runner broke"
            raise RuntimeError(message)
        match command:
            case ("docker", "pull", *_) if self.pull_error:
                return 1, "", self.pull_error
            case ("k3d", "image", "import", image, *_):
                return self.import_answer(command, image, stderr_into_stdout)
            case ("docker", "exec", _, "crictl", "inspecti", image):
                return self.node_image_answer(image)
            case ("timeout", _, "kubectl", "cp", *_):
                return self.copy_exit, "", self.copy_stderr
            case ("timeout", _, "kubectl", "exec", *_):
                return self.exec_answer(command)
            case _:
                answer = self.answer(command)
                return (1, "", "") if answer is None else (0, answer, "")

    def import_answer(
        self,
        command: tuple[str, ...],
        image: str,
        stderr_into_stdout: bool,  # noqa: FBT001 - Effects.run's positional shape
    ) -> tuple[int, str, str]:
        """How `k3d image import` ends and what it writes, merged or split."""
        if self.store_import_error and image == self.failing_store_image:
            failed = (f"ERRO[0000] {self.store_import_error}\n",)
            return 1, *k3d_output(failed, stderr_into_stdout=stderr_into_stdout)
        self.container_effect(command)
        return 0, *k3d_output(self.import_log, stderr_into_stdout=stderr_into_stdout)

    def node_image_answer(self, image: str) -> tuple[int, str, str]:
        """How the node's runtime answers a look for the image, by its looks so far."""
        missing = 1, "", f'no such image "{image}" present'
        if image != self.failing_store_image:
            return 0, "{}", ""
        imports = len(self.issued("k3d", "image", "import", image))
        if imports <= self.store_imports_lost:
            return missing
        looks = len(
            self.issued("docker", "exec", self.node_name, "crictl", "inspecti", image)
        )
        found = self.store_found_in_node
        if found is not None and looks - self.store_imports_lost > found:
            return missing
        return 0, "{}", ""

    @property
    def failing_store_image(self) -> str:
        return self.store_images[self.failing_store]

    def reach_end(self, target: str) -> str:
        """How a new connection to HOST:PORT ends, by what listens there."""
        host, _, port = target.rpartition(":")
        if host == "127.0.0.1":
            return self.listener_loopback_end
        if port == str(INBOUND_PORT):
            return self.inbound_end
        return self.witness_end(host)

    def witness_end(self, host: str) -> str:
        """How the relabelled web pod's connection to a worker's build port ends.

        It connects until the cluster drops it for the relabel, and times out
        until the cluster admits it once the probe's policy is applied.
        """
        issued = [command for command, _, _ in self.commands]
        reaches = [
            at
            for at, command in enumerate(issued)
            if command[2:4] == ("kubectl", "exec")
            and command[-2] == "reach"
            and command[-1].rpartition(":")[0] == host
        ]
        applied = [
            at
            for at, (command, _, stdin) in enumerate(self.commands)
            if command[:2] == ("kubectl", "apply") and "probe-inbound" in (stdin or "")
        ]
        if applied and self.timeouts_before_the_admission is not None:
            since = [at for at in reaches if at > applied[0]]
            if len(since) > self.timeouts_before_the_admission:
                return "connected"
        if self.connects_before_the_drop is None:
            return "connected"
        return (
            "timeout" if len(reaches) > self.connects_before_the_drop else "connected"
        )

    def exec_answer(self, command: tuple[str, ...]) -> tuple[int, str, str]:
        """What a program sent into a pod prints, by its container and arguments."""
        arguments = command[command.index("-") + 1 :]
        if arguments[:1] == ("reach",):
            return 0, json.dumps({"end": self.reach_end(arguments[-1])}), ""
        if command[command.index("--container") + 1] != "web":
            if arguments == ("listen",):
                return 0, json.dumps({"port": INBOUND_PORT}), ""
            ends = {
                "loopback": self.egress_loopback,
                **dict.fromkeys(arguments, self.egress_off_loopback),
            }
            return (
                0,
                self.egress_stdout
                or json.dumps(dict.fromkeys(self.egress_users, ends)),
                "",
            )
        if command[-2:] != ("build", "export"):
            return 0, self.hello_stdout, ""
        workers_up = self.workers_up()
        if workers_up and self.build_over_workers_breaks:
            message = "the fake runner broke"
            raise RuntimeError(message)
        shown = self.client_stdout if workers_up else self.outage_stdout
        return self.client_exit, "" if self.client_exit else shown, self.client_stderr

    def answer(self, command: tuple[str, ...]) -> str | None:
        match command:
            case ("kubectl", "kustomize", *_):
                return self.render(command)
            case ("kubectl", "get", "nodes", *_):
                return self.nodes_json()
            case (
                ("kubectl", "get", "--raw" | "events", *_)
                | ("docker", "exec", _, "crictl", "images")
                | ("docker", "exec", _, "ctr", "images", "ls", "-q")
                | ("df", *_)
            ):
                return self.node_answer(command)
            case ("kubectl", "get", "pod" | "pods", *_) | ("kubectl", "logs", *_):
                return self.pod_answer(command)
            case ("kubectl", "delete", "statefulset", *_):
                self.cell += 1
            case ("k3d", *_) | ("docker", *_):
                return self.container_answer(command)
            case _:
                pass
        return ""

    def node_answer(self, command: tuple[str, ...]) -> str | None:
        """What the node shows: its kubelet config, its events and images, the disk."""
        configz = f"/api/v1/nodes/{self.node_name}/proxy/configz"
        match command:
            case ("kubectl", "get", "--raw", path):
                return json.dumps(self.kubelet) if path == configz else None
            case ("kubectl", "get", "events", "--all-namespaces", *_):
                return self.node_events
            case ("docker", "exec", name, "crictl", "images"):
                return self.node_images if name == self.node_name else None
            case ("docker", "exec", name, "ctr", "images", "ls", "-q"):
                return self.node_containerd_images if name == self.node_name else None
            case ("df", "-h"):
                return self.disk_use
            case _:
                return ""

    def container_effect(self, command: tuple[str, ...]) -> None:
        """What a k3d or docker command changes: the cluster, its volume, its tools."""
        match command:
            case ("k3d", "cluster", "create" | "delete", *_):
                self.cluster_up = command[2] == "create"
                self.volume_up = (self.volume_up and self.tools_up) or self.cluster_up
            case ("k3d", "image", "import", *_) if self.import_interrupted:
                self.tools_up = True
                raise SystemExit(128 + signal.SIGTERM)
            case ("docker", "rm", "--force", name) if name == self.tools_name:
                self.tools_up = False
            case ("docker", "volume", "rm", "--force", _):
                self.volume_up = False
            case ("docker", *_):
                self.image_effect(command)
            case _:
                pass

    def image_effect(self, command: tuple[str, ...]) -> None:
        """What a docker image command changes: the probe image and the store images."""
        match command:
            case ("docker", "build", *_):
                self.image_built = True
            case ("docker", "tag", _, name) if name == self.probe_image:
                self.image_built = True
            case ("docker", "image", "rm", *_, name) if name == self.probe_image:
                self.image_built = False
            case ("docker", "tag", _, name) if name in self.store_images:
                self.stores_present.add(name)
            case ("docker", "image", "rm", *_, name) if name in self.store_images:
                self.stores_present.discard(name)
            case _:
                pass

    def container_answer(self, command: tuple[str, ...]) -> str:
        """What k3d and docker print: the listings follow the cluster and the image."""
        self.container_effect(command)
        match command:
            case ("k3d", "cluster", "list", *_):
                own = f"{self.cluster_name}  1/1  0/0  true"
                return self.listing("cluster", own, exists=self.cluster_up)
            case ("docker", "ps", *_):
                serverlb = f"k3d-{self.cluster_name}-serverlb"
                own = [serverlb, *([] if "node" in self.omitted else [self.node_name])]
                shown = self.listing(
                    "container",
                    *own,
                    exists=self.cluster_up,
                    stray=self.stray_container,
                )
                return shown + (f"{self.tools_name}\n" if self.tools_up else "")
            case ("docker", "volume", "ls", *_):
                own = f"k3d-{self.cluster_name}-images"
                return self.listing("volume", own, exists=self.volume_up)
            case ("docker", "image", "inspect", *_):
                shown = self.repo_digests
                return json.dumps([self.production_image] if shown is None else shown)
            case ("docker", "image", "ls", *_, repository):
                probe = repository == self.probe_image.split(":")[0]
                own = self.listing("tag", self.probe_image, exists=self.image_built)
                stores = "".join(
                    self.listing("tag", name, exists=name in self.stores_present)
                    for name in self.store_images
                )
                return (own + stores) if probe else ""
            case _:
                pass
        return ""

    @property
    def tools_name(self) -> str:
        return f"k3d-{self.cluster_name}-tools"

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
        if command[0] == "timeout" and command[2:5] == (
            "kubectl",
            "logs",
            "--selector",
        ):
            return self.followed_worker_lines(self.spent_lives())
        if not (
            command[0] == "timeout"
            and command[1].isdigit()
            and command[2:5] == ("kubectl", "get", "pods")
        ):
            message = f"the fake does not understand the watch {command}"
            raise AssertionError(message)
        return self.stream(self.streams[self.cell])

    def followed_worker_lines(self, lives_before: int) -> Generator[str]:
        """The ok lines of the builds that began after the follow did."""
        ended = json.dumps(
            {"event": CONNECTION_EVENT, "service": SERVICE_NAME, "outcome": Outcome.OK}
        )
        lives = self.spent_lives() - lives_before
        if self.workers_log_only_the_first_build and lives_before:
            lives = 0
        for _ in range(lives):
            yield f"{ended}\n"

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

    def pod_answer(self, command: tuple[str, ...]) -> str:
        """What kubectl prints when it gets or logs Pods, by name or by selector."""
        verb, target = command[1], command[2]
        if verb == "logs":
            if target == "--selector":
                if command[3].startswith("app.kubernetes.io/name=build-worker"):
                    return ""
                return "the web's previous life" if "--previous" in command else "log"
            self.pod_named(target)
            return self.twin_log
        if target == "pods":
            selector = command[command.index("--selector") + 1]
            workers = selector.startswith("app.kubernetes.io/name=build-worker")
            return json.dumps(
                {"items": self.worker_pods() if workers else self.web_pods()}
            )
        return json.dumps(self.pod_named(command[3]))

    def workers_up(self) -> bool:
        """The worker's StatefulSet was applied after the web's Deployment."""
        deployed = False
        for command, _, stdin in self.commands:
            if command[:2] == ("kubectl", "apply"):
                deployed = deployed or '"kind": "Deployment"' in (stdin or "")
                if deployed and '"kind": "StatefulSet"' in (stdin or ""):
                    return True
        return False

    def spent_lives(self, commands: list[Issued] | None = None) -> int:
        """How many times the worker pods were all replaced, over the commands given."""
        spent = 0
        for command, _, _ in self.commands if commands is None else commands:
            if (
                command[2:4] == ("kubectl", "exec")
                and command[command.index("--container") + 1] == "web"
            ):
                spent += command[-2:] == ("build", "export") or self.hello_spends_a_life
        return spent

    def lives_at_cordon(self) -> int | None:
        """The spent lives when the node was cordoned, while it still is; else None."""
        held = None
        for index, (command, _, _) in enumerate(self.commands):
            if command[:2] == ("kubectl", "cordon"):
                held = self.spent_lives(self.commands[:index])
            elif command[:2] == ("kubectl", "uncordon"):
                held = None
        return held

    def pod(
        self, name: str, uid: str, created: int, *, ready: bool, scheduled: bool = True
    ) -> Document:
        """A Pod as kubectl lists it: its uid, age, node, Ready condition and address.

        An unscheduled Pod has no node name and is not Ready.
        """
        condition = {
            "type": "Ready",
            "status": "True" if ready and scheduled else "False",
            "lastTransitionTime": stamp(created + READY_AFTER_SECONDS),
        }
        web: Document = {"name": "web", "restartCount": self.web_restarts}
        return {
            "metadata": {"name": name, "uid": uid, "creationTimestamp": stamp(created)},
            "spec": {"nodeName": self.node_name} if scheduled else {},
            "status": {
                "conditions": [condition],
                "containerStatuses": [web],
                "podIP": f"10.1.{created}.{len(name)}",
            },
        }

    def web_pods(self) -> list[Document]:
        """The web's Pods: one, then two once the Deployment is scaled."""
        scaled = any(
            c[:3] == ("kubectl", "scale", "deployment") for c, _, _ in self.commands
        )
        names = WEB_POD_NAMES if scaled else WEB_POD_NAMES[:1]
        return [
            self.pod(name, f"{name}-uid", 30 * number, ready=not self.web_never_ready)
            for number, name in enumerate(names)
        ]

    def worker_pods(self) -> list[Document]:
        """The worker Pods: none before they are applied, new ones after each life.

        A pod that replaced one while the node is cordoned stays unscheduled and
        not Ready, apart from the last one a broken hold binds.
        """
        if not self.workers_up():
            return []
        generation = 0 if self.workers_keep_uids else self.spent_lives()
        held_above = self.lives_at_cordon()
        held = held_above is not None and generation > held_above
        last = len(WORKER_POD_NAMES) - 1
        return [
            self.pod(
                name,
                f"{name}-generation-{generation}",
                100 + 10 * generation + number,
                ready=not self.workers_never_ready and not held,
                scheduled=not held or (self.hold_breaks and number == last),
            )
            for number, name in enumerate(WORKER_POD_NAMES)
        ]

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


def k3d_output(log: tuple[str, ...], *, stderr_into_stdout: bool) -> tuple[str, str]:
    """The stdout and stderr of a k3d command that writes the log's lines in order.

    k3d writes its ERRO and WARN lines to stderr and the rest to stdout.
    """
    if stderr_into_stdout:
        return "".join(log), ""
    errors = [line for line in log if line.startswith(("ERRO", "WARN"))]
    rest = [line for line in log if line not in errors]
    return "".join(rest), "".join(errors)


@dataclass
class Fixture:
    """A run over a fake world, with the driver that drives it."""

    driver: ModuleType
    world: World
    run: Any
    reference: str

    def drive(self, *, released: bool = False) -> None:
        """Drive the run, over the published image if `released`."""
        image = self.driver.released_image(self.reference) if released else None
        self.driver.drive(self.run, image)


def platform_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "platform"
    (checkout / "infrastructure" / "namespaces").mkdir(parents=True, exist_ok=True)
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


def fake_effects(
    cluster_module: ModuleType, world: World, *, head: str | None, missing: str | None
) -> object:
    """The effects of a run whose every outside is the world."""
    ticks = [0.0]
    generated = itertools.count()

    def sleep(seconds: float) -> None:
        ticks[0] += seconds

    def run_command(
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
        stderr_into_stdout: bool,  # noqa: FBT001 - Effects.run's positional shape
    ) -> object:
        returncode, stdout, stderr = world(
            command, environment, stdin, stderr_into_stdout
        )
        return cluster_module.Completed(returncode, stdout, stderr)

    def which(tool: str) -> str | None:
        return None if tool == missing else f"/bin/{tool}"

    def head_of(_checkout: Path) -> str:
        return head or cluster_module.PLATFORM_COMMIT

    return cluster_module.Effects(
        run=run_command,
        watch=world.watch,
        which=which,
        platform_head=head_of,
        free_port=lambda: 6550,
        monotonic=lambda: ticks[0],
        sleep=sleep,
        hold_signals=world.hold_signals,
        secret=lambda: f"{GENERATED_VALUE}{next(generated)}",
    )


def fixture_maker(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    driver = load_script("probe_build_worker_k3d")
    cluster_module = load_script("probe_build_worker_k3d_cluster")
    lives_module = load_script("probe_build_worker_k3d_lives")
    manifests_module = load_script("probe_build_worker_k3d_manifests")
    web_module = load_script("probe_build_worker_web")
    web_phase = load_script("probe_build_worker_k3d_web")
    production_image = manifests_module.PRODUCTION_IMAGE + DIGEST

    def make(
        world: World | None = None,
        *,
        head: str | None = None,
        missing: str | None = None,
    ) -> Fixture:
        world = world or World()
        effects = fake_effects(cluster_module, world, head=head, missing=missing)
        run = cluster_module.Run(
            identifier=RUN_IDENTIFIER,
            directory=tmp_path / "run",
            repository=tmp_path / "presentator",
            platform=platform_checkout(tmp_path),
            effects=effects,
        )
        world.node_name, world.probe_image = run.node, run.tag
        world.store_images = (run.redis_image, run.postgres_image, run.garage_image)
        world.cluster_name = run.name
        world.production_image, world.twin_pod = production_image, driver.TWIN_POD
        world.residue_pods = residue_pods(load_script, driver, world.outcomes)
        world.client_stdout = world.client_stdout or client_output(
            delivered_outcome(web_module)
        )
        world.outage_stdout = world.outage_stdout or client_output(
            outage_outcome(web_module)
        )
        world.hello_stdout = world.hello_stdout or json.dumps(web_phase.HELLOS)
        world.streams = world.streams or [
            lives_stream(1, Predicate.EGRESS, version=Version(init=False)),
            lives_stream(lives_module.LIVES, None),
        ]
        reference = f"{manifests_module.PRODUCTION_IMAGE}:{RELEASED_VERSION}{DIGEST}"
        return Fixture(driver, world, run, reference)

    return make


BROKEN_FORMS: list[tuple[str, Callable[[str], str]]] = [
    ("no digest", lambda reference: reference.replace(DIGEST, "")),
    ("no tag", lambda reference: reference.replace(f":{RELEASED_VERSION}", "")),
    ("another repository", lambda reference: reference.replace("overnightworks", "x")),
    ("the local name", lambda reference: reference.rsplit("/", 1)[-1]),
    ("uppercase digest", lambda reference: reference.replace("a" * 64, "A" * 64)),
    ("short digest", lambda reference: reference[:-1]),
    ("long digest", lambda reference: f"{reference}a"),
    ("another algorithm", lambda reference: reference.replace("sha256", "sha512")),
    ("trailing text", lambda reference: f"{reference} --privileged"),
    ("trailing newline", lambda reference: f"{reference}\n"),
]


def issued_commands(world: World) -> list[tuple[tuple[str, ...], str]]:
    """Each command of the run with its stdin, in the order it was issued."""
    return [(command, stdin or "") for command, _, stdin in world.commands]


def apply_positions(world: World, kind: str) -> list[int]:
    """The positions of the applies whose stream holds a document of the kind."""
    return [
        at
        for at, (command, stdin) in enumerate(issued_commands(world))
        if command[:2] == ("kubectl", "apply") and f'"kind": "{kind}"' in stdin
    ]


def command_positions(world: World, *prefix: str) -> list[int]:
    """The positions of the commands that start with the words."""
    return [
        at
        for at, (command, _) in enumerate(issued_commands(world))
        if command[: len(prefix)] == prefix
    ]
