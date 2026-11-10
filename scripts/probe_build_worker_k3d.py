r"""Drive the build worker's pod shape in an ordinary k3d cluster.

Usage: probe_build_worker_k3d.py pin
       probe_build_worker_k3d.py drive --platform CHECKOUT

`pin` prints the one platform commit this probe is written against; CI checks
platform out at it. `drive` refuses unless CHECKOUT is at that commit and k3d,
kubectl and yq are on PATH, proves that the overlay rendering the probe image
differs from platform's own render only in the two presentator image fields,
then drives two cells in a single-server k3d cluster at platform's K3S_VERSION.
Cell 7 applies the worker StatefulSet of `apps/presentator` without its init
container and expects the self-check to refuse with no-route. Cell 8 applies it
unchanged and expects remove-link to end 0 and the self-check to refuse with
kubernetes-unproven. A cell that fails prints the Namespace's events and the
output of a twin Pod of the same spec; the run fails after both cells ran.

Every subprocess runs with `KUBECONFIG` set to a file in the run's temporary
directory, so nothing reads or writes the operator's kubeconfig, and no API
write happens before the cluster's one node is proven to be the run's own. The
teardown runs on every way out, SIGTERM and SIGINT included. CI runs it on a
clean runner; a local run takes the probe-stack lock:

    flock -o -w 3600 /tmp/probe-stack.lock setpriv --pdeathsig TERM \
        timeout -k 30 1800 python scripts/probe_build_worker_k3d.py drive \
        --platform CHECKOUT
"""

import copy
import json
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
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from probe_build_worker_harness import REPOSITORY, refuse, say
from ruamel.yaml import YAML

from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import Predicate

PLATFORM_COMMIT = "ccf8bc52d718deb18ab9e28f3c94dd9dd11ab966"
TOOLS = ("k3d", "kubectl", "yq")
NAMESPACE = "presentator"
IN_NAMESPACE = ("--namespace", NAMESPACE)
PRODUCTION_IMAGE = "ghcr.io/overnightworks/presentator"
PROBE_REPOSITORY = "presentator-k3d-probe"
PRESENTATOR_CONTAINERS = 2
WORKER_POD = "build-worker-0"
WORKER_CONTAINER = "worker"
INIT_CONTAINER = "remove-link"
TWIN_POD = "build-worker-twin"
TERMINATION_WINDOW_SECONDS = 180
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

    def __call__(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        stdin: str | None,
    ) -> Completed:
        """Run the command to its end with exactly this environment."""
        with subprocess.Popen(  # noqa: S603 - argv is literal words and run values and runs without a shell
            command,
            env=dict(environment),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as child:
            self.child = child
            stdout, stderr = child.communicate(stdin)
        self.child = None
        return Completed(child.returncode, stdout, stderr)

    def kill_child(self) -> None:
        """Kill the running child and reap it."""
        if self.child is not None:
            self.child.kill()
            self.child.wait()


@dataclass(frozen=True)
class Effects:
    """Every way the driver reaches outside itself."""

    run: Callable[[tuple[str, ...], Mapping[str, str], str | None], Completed]
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
    def kubeconfig(self) -> Path:
        """The only kubeconfig any command of this run sees."""
        return self.directory / "kubeconfig"

    def __call__(
        self, words: str, *arguments: str, stdin: str | None = None, check: bool = True
    ) -> Completed:
        """Run the fixed `words` and `arguments` with the run's KUBECONFIG.

        A failing command refuses unless `check` is off.
        """
        environment = {**os.environ, "KUBECONFIG": str(self.kubeconfig)}
        command = (*words.split(), *arguments)
        done = self.effects.run(command, environment, stdin)
        if check and done.returncode:
            refuse(f"{words} ended {done.returncode}: {done.stderr[-2000:]}")
        return done


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


def node_problem(nodes: Document, cluster: str, k3s_version: str) -> str | None:
    """Why the cluster is not the run's own single Ready node at K3S_VERSION."""
    items = nodes["items"]
    expected = f"k3d-{cluster}-server-0"
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


def _containers(document: Document) -> list[Document]:
    template = document.get("spec", {}).get("template", {})
    return template.get("spec", {}).get("containers", [])


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


def _wrong_image(image: str) -> str:
    if image.startswith(PRODUCTION_IMAGE):
        return f"is left at {image}"
    return "carries a digest" if "@" in image else f"names {image}"


def _swap_images(base: list[Document], overlay: list[Document], image: str) -> str:
    """Drop the presentator image fields of both renders; say why one is wrong."""
    swapped = 0
    for before, after in zip(base, overlay, strict=True):
        pairs = zip(_containers(before), _containers(after), strict=False)
        for original, replaced in pairs:
            if not original["image"].startswith(PRODUCTION_IMAGE):
                continue
            if replaced["image"] != image:
                where = f"{after['kind']} {after['metadata']['name']}"
                reason = _wrong_image(replaced["image"])
                return f"{where} container {replaced['name']} {reason}, not {image}"
            del original["image"], replaced["image"]
            swapped += 1
    if swapped != PRESENTATOR_CONTAINERS:
        return f"{swapped} presentator images swapped, expected two"
    return ""


def render_problem(
    base: list[Document], overlay: list[Document], image: str
) -> str | None:
    """Why the overlay differs from the base beyond the two presentator images."""
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
    swap = {"name": PRODUCTION_IMAGE, "newName": PROBE_REPOSITORY}
    (overlay / "kustomization.yaml").write_text(
        json.dumps(
            {
                "resources": [os.path.relpath(app, overlay)],
                "images": [{**swap, "newTag": run.identifier}],
            }
        )
    )
    base = documents_of(run("kubectl kustomize", app).stdout)
    rendered = documents_of(run("kubectl kustomize", str(overlay)).stdout)
    if problem := render_problem(base, rendered, run.tag):
        refuse(f"the overlay render: {problem}")
    say("cell 5: the overlay changes only the two presentator images")
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
    if problem := node_problem(nodes, run.name, k3s_version):
        refuse(f"cluster identity: {problem}")
    say(f"identity holds: node k3d-{run.name}-server-0 is Ready at {k3s_version}")


@dataclass(frozen=True)
class Expectation:
    """What a cell's worker pod must show."""

    refusal: Predicate
    init_container: bool


def _terminated(statuses: list[Document], name: str) -> tuple[Document, bool] | None:
    """A container's termination, and whether it ended an earlier run."""
    for status in statuses:
        if status["name"] == name:
            if "terminated" in status.get("state", {}):
                return status["state"]["terminated"], False
            if "terminated" in status.get("lastState", {}):
                return status["lastState"]["terminated"], True
    return None


def _worker_termination(pod: Document) -> tuple[Document, bool] | None:
    statuses = pod.get("status", {}).get("containerStatuses", [])
    return _terminated(statuses, WORKER_CONTAINER)


def _init_problem(pod: Document, expected: Expectation) -> str | None:
    shows = bool(pod["spec"].get("initContainers"))
    shows = shows or "initContainerStatuses" in pod["status"]
    if shows != expected.init_container:
        return f"the Pod {'shows' if shows else 'lacks'} an init container"
    if not expected.init_container:
        return None
    ended = _terminated(pod["status"]["initContainerStatuses"], INIT_CONTAINER)
    if ended is None or ended[0]["exitCode"] != 0:
        return f"{INIT_CONTAINER} ended as {ended}, expected exit status 0"
    return None


def worker_problem(pod: Document, log: str, expected: Expectation) -> str | None:
    """Why the worker pod is not what the cell expects; names what it showed."""
    if problem := _init_problem(pod, expected):
        return problem
    worker = _worker_termination(pod)
    status = worker[0]["exitCode"] if worker else None
    if status != REFUSAL_EXIT_STATUS:
        return f"the worker ended with exit status {status}, not {REFUSAL_EXIT_STATUS}"
    if f"start refused: {expected.refusal}" not in log:
        return (
            f"the log lacks 'start refused: {expected.refusal}': {log.strip()[-500:]}"
        )
    return None


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


def drive_cell(run: Run, number: int, manifest: str, expected: Expectation) -> str:
    """Apply the manifest and judge the worker's first termination; give the problem."""
    run("kubectl apply --filename -", stdin=manifest)
    pod, ended = await_pod(
        run, WORKER_POD, lambda seen: _worker_termination(seen) is not None
    )
    problem = (
        f"no termination within {TERMINATION_WINDOW_SECONDS} s: {pod.get('status')}"
    )
    if ended:
        earlier = _worker_termination(pod)
        flags = ("--previous",) if earlier and earlier[1] else ()
        log = run(
            "kubectl logs", WORKER_POD, "-c", WORKER_CONTAINER, *flags, *IN_NAMESPACE
        ).stdout
        problem = worker_problem(pod, log, expected) or ""
    if not problem:
        say(f"cell {number}: exit {REFUSAL_EXIT_STATUS}, refused {expected.refusal}")
        return ""
    say(f"cell {number}: FAILED, {problem}")
    diagnose(run, manifest)
    return f"cell {number}: {problem}"


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
    rendered = as_stream(overlay)
    control = run(
        "yq",
        'select(.kind != "Deployment") | del(.spec.template.spec.initContainers)',
        stdin=rendered,
    ).stdout
    worker = run("yq", 'select(.kind == "StatefulSet")', stdin=rendered).stdout
    bare = Expectation(Predicate.NO_ROUTE, init_container=False)
    problems = [drive_cell(run, 7, control, bare)]
    run("kubectl delete statefulset --wait=true", "build-worker", *IN_NAMESPACE)
    run("kubectl wait --for=delete --timeout=60s", f"pod/{WORKER_POD}", *IN_NAMESPACE)
    linked = Expectation(Predicate.KUBERNETES_UNPROVEN, init_container=True)
    problems.append(drive_cell(run, 8, worker, linked))
    return [problem for problem in problems if problem]


def teardown(run: Run) -> None:
    """Remove the cluster, the image and the directory, whatever each step finds."""
    run("k3d cluster delete", run.name, check=False)
    run("docker image rm --force", run.tag, check=False)
    shutil.rmtree(run.directory, ignore_errors=True)


def drive(run: Run) -> None:
    """Drive both cells of the pod shape; always remove what the run made."""
    k3s_version = preflight(run)
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
        runner, shutil.which, _git_head, _free_port, time.monotonic, time.sleep
    )
    drive(
        Run(
            identifier=uuid.uuid4().hex[:8],
            directory=Path(tempfile.mkdtemp(prefix="probe-k3d-")),
            repository=REPOSITORY,
            platform=checkout.resolve(),
            effects=effects,
        )
    )


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["pin"]:
            say(PLATFORM_COMMIT)
        case ["drive", "--platform", checkout]:
            drive_for_real(Path(checkout))
        case _:
            sys.exit(__doc__)
