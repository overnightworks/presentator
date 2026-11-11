"""The k3d probe's runner, run, preflight, cluster identity and teardown.

Every command of a run goes through `Run` with the run's own kubeconfig, cache
and `KUBERC=off`, so nothing reads or writes the operator's kubeconfig. The
cluster is proven to be the run's own before any API write, `leftovers` is the
one listing of what a run may leave behind, and `teardown` ignores further
signals, then removes the cluster, the image and the directory whatever each one
finds.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tomllib
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from probe_build_worker_harness import refuse, say
from ruamel.yaml import YAML

PLATFORM_COMMIT = "8fc9046d873ffdb0c3b0732120d9655d8591ff9a"
TOOLS = ("k3d", "kubectl", "docker")
NAMESPACE = "presentator"
IN_NAMESPACE = ("--namespace", NAMESPACE)
PROBE_REPOSITORY = "presentator-k3d-probe"
# k3d labels every container and volume of a cluster with it.
K3D_LABEL = "app=k3d"
# The production image's name, which the probe never builds.
PRODUCTION_REPOSITORY = "presentator"

Document = dict[str, Any]


@dataclass(frozen=True)
class Completed:
    """What a subprocess ended with."""

    returncode: int
    stdout: str
    stderr: str


class SubprocessRunner:
    """Runs one subprocess at a time and can kill the one that is running."""

    def __init__(self) -> None:
        """Start with no child running."""
        self.child: subprocess.Popen[str] | None = None

    @staticmethod
    def _start(
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: int,
        stderr: int | None,
    ) -> subprocess.Popen[str]:
        # A session of its own makes the child's process group killable as one:
        # `timeout` and the program it runs die together.
        return subprocess.Popen(  # noqa: S603 - argv is literal words and run values and runs without a shell
            command,
            env=dict(environment),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            start_new_session=True,
        )

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
    ) -> Completed:
        """Run the command to its end with exactly this environment."""
        piped = subprocess.PIPE if stdin is not None else subprocess.DEVNULL
        with self._start(command, environment, piped, subprocess.PIPE) as child:
            self.child = child
            stdout, stderr = child.communicate(stdin)
        self.child = None
        return Completed(child.returncode, stdout, stderr)

    def stream(
        self, command: tuple[str, ...], environment: Mapping[str, str]
    ) -> Generator[str]:
        """The lines a command prints as it prints them; closing it kills the command.

        The command's own errors go to this process's stderr.
        """
        with self._start(command, environment, subprocess.DEVNULL, None) as child:
            self.child = child
            try:
                yield from child.stdout or ()
            finally:
                self.kill_child()
                self.child = None

    def kill_child(self) -> None:
        """Kill the running child's process group and reap the child."""
        if self.child is not None and self.child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.child.pid, signal.SIGKILL)
            self.child.wait()


@dataclass(frozen=True)
class Effects:
    """Every way the driver reaches outside itself."""

    run: Callable[[tuple[str, ...], Mapping[str, str], str | None], Completed]
    watch: Callable[[tuple[str, ...], Mapping[str, str]], Generator[str]]
    which: Callable[[str], str | None]
    platform_head: Callable[[Path], str]
    free_port: Callable[[], int]
    monotonic: Callable[[], float]
    sleep: Callable[[float], None]
    hold_signals: Callable[[], None]


@dataclass(frozen=True)
class Run:
    """One drive: its names, its temporary directory and its effects."""

    identifier: str
    directory: Path
    repository: Path
    platform: Path
    effects: Effects

    @property
    def name(self) -> str:
        """The k3d cluster's name."""
        return f"probe-{self.identifier}"

    @property
    def tag(self) -> str:
        """The probe image's name and tag, never the production image's."""
        return f"{PROBE_REPOSITORY}:{self.identifier}"

    @property
    def node(self) -> str:
        """The name k3d gives the cluster's one server node."""
        return f"k3d-{self.name}-server-0"

    @property
    def kubeconfig(self) -> Path:
        """The only kubeconfig any command of this run sees."""
        return self.directory / "kubeconfig"

    @property
    def environment(self) -> dict[str, str]:
        """The environment of every command: the run's own kubeconfig and cache."""
        return {
            **os.environ,
            "KUBECONFIG": str(self.kubeconfig),
            "KUBECACHEDIR": str(self.directory / "cache"),
            "KUBERC": "off",
        }

    def __call__(
        self, words: str, *arguments: str, stdin: str | None = None, check: bool = True
    ) -> Completed:
        """Run the fixed `words` and `arguments` in the run's environment.

        A failing command refuses unless `check` is off.
        """
        command = (*words.split(), *arguments)
        done = self.effects.run(command, self.environment, stdin)
        if check and done.returncode:
            refuse(f"{words} ended {done.returncode}: {done.stderr[-2000:]}")
        return done

    def watch(self, words: str, *arguments: str) -> Generator[str]:
        """The lines of the fixed `words` and `arguments` as the command prints them."""
        return self.effects.watch((*words.split(), *arguments), self.environment)


def documents_of(text: str) -> list[Document]:
    """The YAML documents of a stream, empty ones left out."""
    loaded: Iterable[Document | None] = YAML(typ="safe").load_all(text)  # pyright: ignore[reportUnknownMemberType]
    return [document for document in loaded if document]


def as_stream(documents: list[Document]) -> str:
    """The documents as one multi-document stream, which is JSON per document."""
    return "\n---\n".join(json.dumps(document) for document in documents)


def preflight(run: Run) -> str:
    """Refuse unless the checkout is the pin and the tools exist; give K3S_VERSION."""
    head = run.effects.platform_head(run.platform)
    if head != PLATFORM_COMMIT:
        refuse(f"platform is at {head}, this probe needs {PLATFORM_COMMIT}")
    for tool in TOOLS:
        if run.effects.which(tool) is None:
            refuse(f"{tool} is not on PATH")
    mise = tomllib.loads((run.platform / "mise.toml").read_text())
    return mise["env"]["K3S_VERSION"]


def node_problem(nodes: Document, expected: str, k3s_version: str) -> str | None:
    """Why the cluster is not the run's own single Ready node at K3S_VERSION."""
    items = nodes["items"]
    if len(items) != 1:
        return f"the cluster has {len(items)} nodes, expected exactly one"
    name = items[0]["metadata"]["name"]
    conditions = {c["type"]: c["status"] for c in items[0]["status"]["conditions"]}
    version = items[0]["status"]["nodeInfo"]["kubeletVersion"]
    if name != expected:
        return f"the node is named {name}, expected {expected}"
    if conditions.get("Ready") != "True":
        return f"node {name} is not Ready"
    if version != k3s_version:
        return f"node {name} runs {version}, K3S_VERSION is {k3s_version}"
    return None


def start_cluster(run: Run, k3s_version: str) -> None:
    """Create the run's cluster and prove its identity before any write."""
    run(
        "k3d cluster create",
        run.name,
        "--image",
        f"rancher/k3s:{k3s_version.replace('+', '-')}",
        "--api-port",
        f"127.0.0.1:{run.effects.free_port()}",
        "--kubeconfig-update-default=false",
        "--wait",
        "--timeout",
        "3m",
    )
    run.kubeconfig.write_text(run("k3d kubeconfig get", run.name).stdout)
    run.kubeconfig.chmod(0o600)
    run("kubectl wait --for=condition=Ready node --all --timeout=120s")
    nodes = json.loads(run("kubectl get nodes --output json").stdout)
    if problem := node_problem(nodes, run.node, k3s_version):
        refuse(f"cluster identity: {problem}")
    say(f"identity holds: node {run.node} is Ready at {k3s_version}")


@dataclass(frozen=True)
class Leftovers:
    """What the k3d and docker listings show a run may leave behind."""

    clusters: tuple[str, ...]
    containers: tuple[str, ...]
    volumes: tuple[str, ...]
    images: tuple[str, ...]

    def lines(self) -> list[str]:
        """One line per listed thing, naming its kind."""
        kinds = (
            ("cluster", self.clusters),
            ("container", self.containers),
            ("volume", self.volumes),
            ("image", self.images),
        )
        return [f"{kind}: {name}" for kind, names in kinds for name in names]


def leftovers(command: Callable[[str], str]) -> Leftovers:
    """List the clusters, the labelled containers and volumes, and the images.

    `command` runs fixed words and gives what they print. The images are the
    probe's and, as a deny list, the production image's.
    """

    def names(words: str) -> tuple[str, ...]:
        return tuple(command(words).split())

    label = f"--filter label={K3D_LABEL}"
    image = "docker image ls --format {{.Repository}}:{{.Tag}}"
    return Leftovers(
        clusters=tuple(
            line.split()[0]
            for line in command("k3d cluster list --no-headers").splitlines()
        ),
        containers=names(f"docker ps --all --format {{{{.Names}}}} {label}"),
        volumes=names(f"docker volume ls --quiet {label}"),
        images=names(f"{image} {PROBE_REPOSITORY}")
        + names(f"{image} {PRODUCTION_REPOSITORY}"),
    )


def prove_label_filter(run: Run) -> None:
    """Refuse unless the listing shows the run's own cluster, node, a volume and tag.

    The check after a drive reads the same listing, so a filter that matches
    nothing cannot pass it.
    """
    found = leftovers(lambda words: run(words).stdout)
    lacks = [
        what
        for what, holds in (
            (f"the run's cluster {run.name}", run.name in found.clusters),
            (f"the run's node container {run.node}", run.node in found.containers),
            ("a labelled volume", bool(found.volumes)),
            (f"the run's image tag {run.tag}", run.tag in found.images),
        )
        if not holds
    ]
    if lacks:
        refuse(f"label filter: the listing lacks {', '.join(lacks)}")
    say(
        f"label filter holds: {len(found.containers)} containers, "
        f"{len(found.volumes)} volumes of the run's cluster"
    )


def teardown(run: Run) -> None:
    """Ignore further signals, then remove the cluster, the image and the directory.

    Each is attempted whatever the one before did.
    """
    run.effects.hold_signals()
    say(f"teardown: removing cluster {run.name}")
    try:
        run("k3d cluster delete", run.name, check=False)
    finally:
        try:
            run("docker image rm --force", run.tag, check=False)
        finally:
            shutil.rmtree(run.directory, ignore_errors=True)
