r"""Drive the build worker's pod shape in an ordinary k3d cluster.

Usage: probe_build_worker_k3d.py pin
       probe_build_worker_k3d.py probe-repository
       probe_build_worker_k3d.py drive --platform CHECKOUT

`pin` prints the one platform commit this probe is written against; CI checks
platform out at it. `probe-repository` prints the repository of every probe
image, which CI's teardown check lists. `drive` refuses unless CHECKOUT is at
that commit and k3d, kubectl and docker are on PATH, proves that the overlay
rendering the probe image differs from platform's own render only in the
production image fields of every pod template, then drives two cells in a
single-server k3d cluster at platform's K3S_VERSION. A cell applies the
PersistentVolumeClaims, ServiceAccounts and worker StatefulSet of
`apps/presentator`; the applied copy differs from the render only in the worker
container's `terminationMessagePolicy` (so a refusal reaches the terminated
state's message) and, in cell 7, in the deleted init container.

Platform replaces a used pod and its log with it, so a cell judges its lives
from one bounded watch of the worker pod. Cell 7 reads one life without init
container and expects the self-check to refuse with no-route. Cell 8 reads
LIVES lives and expects, per life, a new uid, remove-link ending 0, one
termination of the worker with exit 2 and kubernetes-unproven and restartCount
0; it prints each interval from a life's end to its successor's start, which
must stay below the web's HELLO retry window. A cell that fails prints the
Namespace's events and the output of a twin Pod of the same spec; the run fails
after both cells ran.

Every command runs with `KUBECONFIG` set to a file in the run's temporary
directory, `KUBECACHEDIR` below it and `KUBERC=off`, so nothing reads or writes
the operator's kubeconfig; only the git lookup of CHECKOUT's commit runs
without them. No API write happens before the cluster's one node is proven to
be the run's own. The teardown runs on every way out, SIGTERM and SIGINT
included, which also kill the watch. CI runs it on a clean runner; a local run
takes the probe-stack lock:

    flock -o -w 3600 /tmp/probe-stack.lock setpriv --pdeathsig TERM \
        timeout -k 30 1800 python scripts/probe_build_worker_k3d.py drive \
        --platform CHECKOUT
"""

import contextlib
import copy
import itertools
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Generator, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Self, cast

from probe_build_worker_harness import REPOSITORY, refuse, say
from ruamel.yaml import YAML

from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

PLATFORM_COMMIT = "8fc9046d873ffdb0c3b0732120d9655d8591ff9a"
TOOLS = ("k3d", "kubectl", "docker")
NAMESPACE = "presentator"
IN_NAMESPACE = ("--namespace", NAMESPACE)
PRODUCTION_IMAGE = "ghcr.io/overnightworks/presentator"
PROBE_REPOSITORY = "presentator-k3d-probe"
APPLIED_KINDS = ("PersistentVolumeClaim", "ServiceAccount", "StatefulSet")
WORKER_POD = "build-worker-0"
WORKER_CONTAINER = "worker"
INIT_CONTAINER = "remove-link"
TERMINATION_MESSAGE_POLICY = "FallbackToLogsOnError"
# What the kubelet reports for a container whose sandbox was lost.
LOST_SANDBOX_REASON = "ContainerStatusUnknown"
TWIN_POD = "build-worker-twin"
TERMINATION_WINDOW_SECONDS = 180
LIVES = 4
TIMESTAMP_RESOLUTION_SECONDS = 1
POLL_SECONDS = 2
# The environment names come from python: `env` would print the later lines of
# a multi-line value.
TWIN_SCRIPT = r"""cat /proc/self/mountinfo /proc/self/status /proc/self/limits
cat /sys/fs/cgroup/memory.max
id
stat --format '%n %U:%G %a' /etc/hosts /dev/pts /sys/fs/cgroup
python -c 'import os; print("\n".join(sorted(os.environ)))'"""

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


def _first_difference(before: Any, after: Any, path: str) -> str | None:  # noqa: ANN401 - a parsed YAML value is any scalar, list or mapping
    pairs: list[tuple[Any, Any, str]] = []
    if isinstance(before, dict) and isinstance(after, dict):
        left, right = cast("Document", before), cast("Document", after)
        for key in sorted(left.keys() | right.keys()):
            if key not in left or key not in right:
                return f"{path}.{key}: present on one side only"
            pairs.append((left[key], right[key], f"{path}.{key}"))
    elif isinstance(before, list) and isinstance(after, list):
        before, after = cast("list[Any]", before), cast("list[Any]", after)
        if len(before) != len(after):
            return f"{path}: {len(before)} items against {len(after)}"
        pairs = [
            (b, a, f"{path}[{i}]")
            for i, (b, a) in enumerate(zip(before, after, strict=True))
        ]
    elif before != after:
        return f"{path}: {before!r} against {after!r}"
    return next(filter(None, (_first_difference(*pair) for pair in pairs)), None)


def _pod_specs(document: Document) -> list[Document]:
    """The pod specs of a workload: its template's and a CronJob's job template's."""
    spec = document.get("spec", {})
    templates = [
        spec.get("template"),
        spec.get("jobTemplate", {}).get("spec", {}).get("template"),
    ]
    return [template["spec"] for template in templates if template]


def _wrong_image(image: str) -> str:
    if image.startswith(PRODUCTION_IMAGE):
        return f"is left at {image}"
    return "carries a digest" if "@" in image else f"names {image}"


def _swap_images(base: list[Document], overlay: list[Document], image: str) -> str:
    """Drop the presentator image fields of both renders; say why one is wrong."""
    worker_swapped = False
    for before, after in zip(base, overlay, strict=True):
        where = f"{after['kind']} {after['metadata']['name']}"
        for original_spec, replaced_spec in zip(
            _pod_specs(before), _pod_specs(after), strict=False
        ):
            pairs = zip(
                original_spec["containers"], replaced_spec["containers"], strict=False
            )
            for original, replaced in pairs:
                if not original.get("image", "").startswith(PRODUCTION_IMAGE):
                    continue
                if replaced["image"] != image:
                    reason = _wrong_image(replaced["image"])
                    return f"{where} container {replaced['name']} {reason}, not {image}"
                del original["image"], replaced["image"]
                worker_swapped = worker_swapped or (
                    after["kind"] == "StatefulSet"
                    and original["name"] == WORKER_CONTAINER
                )
    if not worker_swapped:
        return f"the StatefulSet's {WORKER_CONTAINER} is not at {PRODUCTION_IMAGE}"
    return ""


def render_problem(
    base: list[Document], overlay: list[Document], image: str
) -> str | None:
    """Why the overlay differs from the base beyond the production image fields."""
    base, overlay = copy.deepcopy(base), copy.deepcopy(overlay)
    if len(base) != len(overlay):
        return f"the overlay renders {len(overlay)} documents, the base {len(base)}"
    if problem := _swap_images(base, overlay, image):
        return problem
    for before, after in zip(base, overlay, strict=True):
        label = f"{after['kind']} {after['metadata']['name']}"
        if found := _first_difference(before, after, label):
            return found
    return None


def prove_render(run: Run) -> list[Document]:
    """Render the image-only overlay, prove it against the base and give its render."""
    app = str(run.platform / "apps" / "presentator")
    overlay = run.directory / "overlay"
    overlay.mkdir()
    repository, _, tag = run.tag.rpartition(":")
    swap = {"name": PRODUCTION_IMAGE, "newName": repository, "newTag": tag}
    (overlay / "kustomization.yaml").write_text(
        json.dumps({"resources": [os.path.relpath(app, overlay)], "images": [swap]})
    )
    base = documents_of(run("kubectl kustomize", app).stdout)
    rendered = documents_of(run("kubectl kustomize", str(overlay)).stdout)
    if problem := render_problem(base, rendered, run.tag):
        refuse(f"the overlay render: {problem}")
    say("cell 5: the overlay changes only the production image fields")
    return rendered


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
class Expectation:
    """What a cell's worker pods must show."""

    refusal: Predicate
    init_container: bool
    lives: int


@dataclass(frozen=True)
class Termination:
    """One end of the worker container, as the kubelet reported it."""

    exit_code: int
    reason: str
    message: str
    started: str
    finished: str

    @classmethod
    def of(cls, state: Document) -> Self:
        """The termination a `terminated` state describes."""
        return cls(
            exit_code=state["exitCode"],
            reason=state.get("reason", ""),
            message=state.get("message", ""),
            started=state.get("startedAt", ""),
            finished=state.get("finishedAt", ""),
        )


@dataclass(frozen=True)
class Life:
    """What every version of one pod uid showed."""

    uid: str
    has_init: bool = False
    init_exit: int | None = None
    terminations: frozenset[Termination] = frozenset()
    restarts: int = 0
    deleted: bool = False

    def after(self, pod: Document, *, deleted: bool) -> Self:
        """This life with one more version of its pod."""
        status = pod.get("status", {})
        worker = _status_of(status.get("containerStatuses", []), WORKER_CONTAINER)
        init = _status_of(status.get("initContainerStatuses", []), INIT_CONTAINER)
        init_ended = init.get("state", {}).get("terminated")
        ended = (
            worker.get(key, {}).get("terminated") for key in ("state", "lastState")
        )
        return type(self)(
            uid=self.uid,
            has_init=self.has_init
            or bool(pod.get("spec", {}).get("initContainers"))
            or "initContainerStatuses" in status,
            init_exit=init_ended["exitCode"] if init_ended else self.init_exit,
            terminations=self.terminations | {Termination.of(t) for t in ended if t},
            restarts=max(self.restarts, worker.get("restartCount", 0)),
            deleted=self.deleted or deleted,
        )


def _status_of(statuses: list[Document], name: str) -> Document:
    return next((s for s in statuses if s["name"] == name), Document())


def watch_events(lines: Iterable[str]) -> Iterator[Document]:
    """The watch events of kubectl's output, each as soon as its last line arrives.

    kubectl prints the events as concatenated JSON objects, so no line ends one.
    A watch that ends inside an event yields only the events before it, so the
    cell judges the lives read.
    """
    decoder = json.JSONDecoder()
    buffer = ""
    for line in lines:
        buffer = (buffer + line).lstrip()
        while buffer:
            try:
                event, end = decoder.raw_decode(buffer)
            except json.JSONDecodeError:
                break
            yield event
            buffer = buffer[end:].lstrip()


def read_lives(events: Iterable[Document], count: int) -> list[Life]:
    """Fold the events into lives, in order, and stop once `count` lives ended."""
    lives: dict[str, Life] = {}
    for event in events:
        pod = event["object"]
        uid = pod["metadata"]["uid"]
        known = lives.get(uid, Life(uid))
        lives[uid] = known.after(pod, deleted=event["type"] == "DELETED")
        if sum(life.deleted for life in lives.values()) == count:
            break
    return list(lives.values())


def watch_lives(run: Run, expected: Expectation) -> list[Life]:
    """Read the worker pod's lives from one watch that `timeout` bounds."""
    later = (expected.lives - 1) * CONNECT_WINDOW_SECONDS
    budget = math.ceil(TERMINATION_WINDOW_SECONDS + later)
    # A field selector, not the pod's name: the pod may not exist yet.
    lines = run.watch(
        f"timeout {budget} kubectl get pods --field-selector metadata.name={WORKER_POD}"
        " --watch --output-watch-events --output json",
        *IN_NAMESPACE,
    )
    with contextlib.closing(lines):
        return read_lives(watch_events(lines), expected.lives)


def refusal_line(message: str, refusal: Predicate) -> str | None:
    """The message's line that names the refusal, if one does."""
    needle = f"start refused: {refusal}"
    return next((line.strip() for line in message.splitlines() if needle in line), None)


def _worker_reason(life: Life, expected: Expectation) -> str | None:
    if len(life.terminations) != 1:
        count = len(life.terminations)
        return (
            f"the worker terminated {count} times"
            if count
            else "the Pod was deleted before its worker terminated"
        )
    (ended,) = life.terminations
    wrong_status = f"the worker ended with exit status {ended.exit_code}"
    no_line = (
        f"no 'start refused: {expected.refusal}' in {ended.message.strip()[-500:]}"
    )
    reasons = (
        (ended.reason == LOST_SANDBOX_REASON, f"the worker ended as {ended.reason}"),
        (bool(life.restarts), f"restartCount is {life.restarts}, expected 0"),
        (ended.exit_code != REFUSAL_EXIT_STATUS, wrong_status),
        (refusal_line(ended.message, expected.refusal) is None, no_line),
    )
    return next((reason for failed, reason in reasons if failed), None)


def life_problem(life: Life, expected: Expectation) -> str | None:
    """Why one life is not what the cell expects; names its uid."""
    shown = "shows" if life.has_init else "lacks"
    if life.has_init != expected.init_container:
        reason = f"the Pod {shown} an init container"
    elif expected.init_container and life.init_exit != 0:
        reason = f"{INIT_CONTAINER} ended as {life.init_exit}, expected exit status 0"
    else:
        reason = _worker_reason(life, expected)
    return f"uid {life.uid}: {reason}" if reason else None


def lives_problem(lives: list[Life], expected: Expectation) -> str | None:
    """Why the lives read are not the cell's expected ones."""
    if len(lives) != expected.lives or not all(life.deleted for life in lives):
        seen = ", ".join(f"{life.uid} (deleted: {life.deleted})" for life in lives)
        return f"the watch ended after {len(lives)} of {expected.lives} lives: {seen}"
    return next(filter(None, (life_problem(life, expected) for life in lives)), None)


def _worker_end(life: Life) -> Termination:
    (termination,) = life.terminations
    return termination


def interval_problem(number: int, lives: list[Life]) -> str | None:
    """Print each interval from a life's worker end to its successor's start.

    Name the first successor that started at or after the web's retry window.
    """
    ends = [_worker_end(life) for life in lives]
    problem = None
    for position, (before, after) in enumerate(itertools.pairwise(ends), start=1):
        started, finished = after.started, before.finished
        gap = (
            datetime.fromisoformat(started) - datetime.fromisoformat(finished)
        ).total_seconds()
        say(
            f"cell {number}: interval {position}: {gap:g} s, resolution "
            f"{TIMESTAMP_RESOLUTION_SECONDS} s, limit {CONNECT_WINDOW_SECONDS:g} s"
        )
        if gap >= CONNECT_WINDOW_SECONDS and problem is None:
            uid = lives[position].uid
            problem = f"uid {uid}: its worker started {gap:g} s after life {position}"
    return problem


def report_lives(number: int, lives: list[Life], refusal: Predicate) -> None:
    """Print every life's uid and refusal line."""
    for position, life in enumerate(lives, start=1):
        line = refusal_line(_worker_end(life).message, refusal)
        who = f"cell {number}: life {position}, uid {life.uid}"
        say(f"{who}, exit {REFUSAL_EXIT_STATUS}, {line}")


def await_pod(
    run: Run, name: str, done: Callable[[Document], bool]
) -> tuple[Document, bool]:
    """Poll a Pod until `done` or the window passes; give the last Pod seen."""
    started = run.effects.monotonic()
    pod: Document = {}
    while True:
        answer = run(
            "kubectl get pod", name, "--output", "json", *IN_NAMESPACE, check=False
        )
        if answer.returncode == 0:
            pod = json.loads(answer.stdout)
            if done(pod):
                return pod, True
        if run.effects.monotonic() - started >= TERMINATION_WINDOW_SECONDS:
            return pod, False
        run.effects.sleep(POLL_SECONDS)


def twin_pod(manifest: str) -> Document:
    """A Pod of the manifest's worker pod spec that prints its facts, not serves."""
    workers = [d for d in documents_of(manifest) if d["kind"] == "StatefulSet"]
    spec = copy.deepcopy(workers[0]["spec"]["template"]["spec"])
    spec["restartPolicy"] = "Never"
    for container in spec["containers"]:
        container["command"] = ["sh", "-c", TWIN_SCRIPT]
        container.pop("args", None)
    metadata = {"name": TWIN_POD, "namespace": NAMESPACE}
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata, "spec": spec}


def diagnose(run: Run, manifest: str) -> None:
    """Print the Namespace's events and what the manifest's twin Pod prints."""
    sort = "--sort-by=.metadata.creationTimestamp"
    say(f"events:\n{run('kubectl get events', sort, *IN_NAMESPACE).stdout}")
    run("kubectl apply --filename -", stdin=json.dumps(twin_pod(manifest)))
    ended = {"Succeeded", "Failed"}
    _, finished = await_pod(
        run, TWIN_POD, lambda pod: pod.get("status", {}).get("phase") in ended
    )
    said = run("kubectl logs", TWIN_POD, *IN_NAMESPACE, check=False)
    verdict = "ended" if finished else "did not end"
    say(f"twin Pod {verdict}:\n{said.stdout}{said.stderr}")
    run("kubectl delete pod --wait=true", TWIN_POD, *IN_NAMESPACE)


def drive_cell(
    run: Run, number: int, documents: list[Document], expected: Expectation
) -> str:
    """Apply the documents and judge the worker pod's lives; give the problem."""
    manifest = as_stream(documents)
    run("kubectl apply --filename -", stdin=manifest)
    lives = watch_lives(run, expected)
    problem = lives_problem(lives, expected)
    if problem is None:
        report_lives(number, lives, expected.refusal)
        problem = interval_problem(number, lives)
        if problem is None:
            return ""
    say(f"cell {number}: FAILED, {problem}")
    diagnose(run, manifest)
    return f"cell {number}: {problem}"


def applied_documents(
    overlay: list[Document], *, init_container: bool
) -> list[Document]:
    """The overlay's documents the worker pod needs, the worker's message policy set."""
    documents = [
        copy.deepcopy(document)
        for document in overlay
        if document["kind"] in APPLIED_KINDS
    ]
    for document in documents:
        if document["kind"] != "StatefulSet":
            continue
        spec = document["spec"]["template"]["spec"]
        if not init_container:
            del spec["initContainers"]
        for container in spec["containers"]:
            if container["name"] == WORKER_CONTAINER:
                container["terminationMessagePolicy"] = TERMINATION_MESSAGE_POLICY
    return documents


def namespace_document(platform: Path) -> Document:
    """Production's Namespace document for presentator, as the checkout has it."""
    path = platform / "infrastructure" / "namespaces" / "namespaces.yaml"
    return next(
        d
        for d in documents_of(path.read_text())
        if d["kind"] == "Namespace" and d["metadata"]["name"] == NAMESPACE
    )


def drive_cells(run: Run, overlay: list[Document]) -> list[str]:
    """Drive the control, then its twin with the init container; both always run."""
    bare = Expectation(Predicate.NO_ROUTE, init_container=False, lives=1)
    control = applied_documents(overlay, init_container=False)
    problems = [drive_cell(run, 7, control, bare)]
    run("kubectl delete statefulset --wait=true", "build-worker", *IN_NAMESPACE)
    run("kubectl wait --for=delete --timeout=60s", f"pod/{WORKER_POD}", *IN_NAMESPACE)
    linked = Expectation(
        Predicate.KUBERNETES_UNPROVEN, init_container=True, lives=LIVES
    )
    worker = applied_documents(overlay, init_container=True)
    problems.append(drive_cell(run, 8, worker, linked))
    return [problem for problem in problems if problem]


def teardown(run: Run) -> None:
    """Remove the cluster, the image and the directory, whatever each one finds."""
    try:
        run("k3d cluster delete", run.name, check=False)
    finally:
        try:
            run("docker image rm --force", run.tag, check=False)
        finally:
            shutil.rmtree(run.directory, ignore_errors=True)


def drive(run: Run) -> None:
    """Drive both cells of the pod shape; always remove what the run made."""
    k3s_version = preflight(run)
    # mkdir refuses an existing path, so a directory planted at the name stops
    # the run instead of holding its kubeconfig.
    run.directory.mkdir(mode=0o700)
    try:
        overlay = prove_render(run)
        run("docker build --tag", run.tag, str(run.repository))
        start_cluster(run, k3s_version)
        run("k3d image import", run.tag, "--cluster", run.name)
        namespace = json.dumps(namespace_document(run.platform))
        run("kubectl apply --filename -", stdin=namespace)
        problems = drive_cells(run, overlay)
    finally:
        teardown(run)
    if problems:
        refuse("; ".join(problems))


def _git_head(checkout: Path) -> str:
    return subprocess.run(  # noqa: S603 - argv is literal words and the checkout path and runs without a shell
        ("git", "-C", str(checkout), "rev-parse", "HEAD"),  # noqa: S607 - git is picked from the trusted PATH of the machine that runs the probe
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def drive_for_real(checkout: Path) -> None:
    """Drive in this process; SIGTERM and SIGINT kill the child, then tear down."""
    runner = SubprocessRunner()

    def stop(signal_number: int, _frame: object) -> None:
        runner.kill_child()
        sys.exit(128 + signal_number)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    effects = Effects(
        runner,
        runner.stream,
        shutil.which,
        _git_head,
        _free_port,
        time.monotonic,
        time.sleep,
    )
    identifier = uuid.uuid4().hex[:8]
    drive(
        Run(
            identifier=identifier,
            directory=Path(tempfile.gettempdir()) / f"probe-k3d-{identifier}",
            repository=REPOSITORY,
            platform=checkout.resolve(),
            effects=effects,
        )
    )


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["pin"]:
            say(PLATFORM_COMMIT)
        case ["probe-repository"]:
            say(PROBE_REPOSITORY)
        case ["drive", "--platform", checkout]:
            drive_for_real(Path(checkout))
        case _:
            sys.exit(__doc__)
