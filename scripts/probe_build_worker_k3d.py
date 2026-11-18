r"""Drive the build worker's pod shape in an ordinary k3d cluster.

Usage: probe_build_worker_k3d.py pin
       probe_build_worker_k3d.py leftovers
       probe_build_worker_k3d.py drive --platform CHECKOUT [--image REF]

`pin` prints the one platform commit this probe is written against; CI checks
platform out at it. `leftovers` lists the k3d clusters, the containers and
volumes labelled `app=k3d` and the images of the probe and of production,
prints each and ends 1 if any is listed; it needs no cluster, run directory or
kubeconfig, and CI runs it after each drive. `drive` refuses unless CHECKOUT is at
that commit and k3d, kubectl and docker are on PATH, proves that the overlay
rendering the probe image differs from platform's own render only in the
production image fields of every pod template, then drives its cells in a
single-server k3d cluster at platform's K3S_VERSION. Cells 7 and 8 apply the
PersistentVolumeClaims, ServiceAccounts, the workers' Service, NetworkPolicy and
packet-filter ConfigMap and the worker StatefulSet of `apps/presentator`; the
applied copy differs from the render only in the worker container's
`terminationMessagePolicy` (so a refusal reaches the terminated state's
message), in its replicas (one until cell 11), in cell 7 in the deleted init
container, and in cell 8 in the worker container's `args`, which are `--check`.

Platform replaces a used pod and its log with it, so cells 7 and 8 judge their
lives from one bounded watch of the worker pod. Cell 7 reads one life without
init container and expects the self-check to refuse with egress. Cell 8 reads
LIVES lives, each passing the whole self-check under `--check`, and expects,
per life, a new uid, nft-rules ending 0, one termination of the worker with
exit 0 and restartCount 0, which includes the self-check's egress probe: a new
socket of root, the build user and the wire user gets EPERM. It prints each
interval from a life's end to its successor's start, which must stay below the
web's connect window.

Cell 9 runs after cell 8, whether or not it failed, on the StatefulSet's pod
spec of cell 8 with the StatefulSet deleted and its pod gone. It prints the
worker pod's facts (mountinfo, status, limits and the rest of the twin's
script) from one bare Pod, then starts bare Pods one at a time: the
control `nothing`, which plants no residue and ends 0 with the self-check
passed under `--check`, and one twin per residue of
REFUSALS. Each twin plants its residue with the planter, passed as
`python -I -c`, and becomes the worker; it must end with exit 2 and refuse with
the predicate its residue belongs to, with nft-rules 0 and restartCount 0,
judged from its terminated state. A twin that does not end within the
termination window fails the cell and no later twin starts. Every twin is
deleted before the cell returns.

Cells 10 to 13 run after cell 9 on the web as platform renders it, at the pin:
the render's ConfigMaps and Deployment, whose web container's Redis URL names a
throwaway Redis the probe runs in its namespace, a throwaway Postgres there in
place of the render's, named, reached and logged into as the web's database URL
says, which the Deployment's init container migrates before the web starts, and
Secrets the driver generates. The Secrets' names and keys come from the Secret
volumes the web container mounts (the files its environment names under each
mount, the database password among them, and the workers' key that the
`secrets` volume projects), each value from the effect `secret`, 64 hex
characters from `secrets` in a real run; the probe's Postgres reads the
password from the same Secret. The values go to `kubectl apply` on
stdin only and never to a printed line, an error, an argument or a file; they
live in the run's cluster. The Secrets go in before cell 7, as the worker pods
mount the workers' key.

Cell 10 awaits the web Ready with no worker pod and no Service, restartCount 0,
copies `examples/hello-deck` into the web pod's `emptyDir` with `kubectl cp` and
runs the web's own client there with `kubectl exec`: its first step must end
unavailable, an outage with no failure of its own. Cell 11 applies the workers'
documents with two replicas, runs two web pods and builds hello-deck in each
through the one Service; each web's client builds and exports, a worker's life
ends with each step, and the first build's two steps must have replaced both
worker pods, and a sample of the worker pods taken at or after its second step's
admission must show a worker not Ready, so it reached the idle worker while the
first restarted.
Cell 12 runs a program in a web pod that answers the workers'
challenge under a wrong key and under the right key with the other direction's
label: both are refused, a true hello is served, and both worker pods keep their
uids and stay Ready, so no life was spent. Cell 13 runs a program in a worker
pod that, as the build user and as the wire user, opens a new connection to the
loopback, which must connect, and to the web pod and the other worker pod, which
must end in EPERM, and it refuses a result that lacks either user or a target;
the replies of accepted connections passed in cell 11. A listener the cell opens
in the worker pod on a port other than the worker's own must answer on the pod's
loopback and, with a NetworkPolicy of the probe's own admitting the web's pods to
every port, still drop a connection from a web pod: it times out. The policy is
deleted when the connection ended.

With `--image REF` the drive runs on the image `release.yml` published instead
of one it builds, and only on a CI runner. REF is exactly
`ghcr.io/overnightworks/presentator:<tag>@sha256:<64 lowercase hex>`; anything
else is refused before any command runs. The drive pulls the image by that
digest, tags it as the run's probe tag, refuses unless the tag's repo digests
list the published digest and removes the pulled reference again. From the
cluster on it runs the same code for both sources.

A cell that fails prints the Namespace's events and the output of a twin Pod of
the same spec; the run fails after the cells ran.

Every command runs with `KUBECONFIG` set to a file in the run's temporary
directory, `KUBECACHEDIR` below it and `KUBERC=off`, so nothing reads or writes
the operator's kubeconfig; only the git lookup of CHECKOUT's commit runs
without them. No API write happens before the cluster's one node is proven to
be the run's own. Right after that, the same listing as `leftovers` must show
the run's cluster, its node container, a labelled volume and the run's image
tag, so a label filter that matches nothing cannot pass the check after a
drive. The teardown runs on every way out, SIGTERM and SIGINT included, which
also kill the watch. The first signal ignores both signals, so a second one
cannot interrupt the exit it started; the teardown ignores them too, and a stuck
teardown is bounded by the wrapper's timeout. A signal that ends `k3d image import`
leaves its tools container and the images volume, so the teardown removes both.
CI runs it on a clean runner; a local run
starts through the machine's load-gated wrapper:

    probe-run python scripts/probe_build_worker_k3d.py drive \
        --platform CHECKOUT
"""

import copy
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from probe_build_worker_harness import REPOSITORY, refuse, say
from probe_build_worker_k3d_cluster import (
    IN_NAMESPACE,
    NAMESPACE,
    PLATFORM_COMMIT,
    Document,
    Effects,
    Run,
    SubprocessRunner,
    as_stream,
    documents_of,
    leftovers,
    preflight,
    prove_label_filter,
    start_cluster,
    teardown,
)
from probe_build_worker_k3d_lives import (
    LIVES,
    PASSING_EXIT_STATUS,
    TERMINATION_WINDOW_SECONDS,
    WORKER_POD,
    Expectation,
    Life,
    await_pod,
    interval_problem,
    life_problem,
    lives_problem,
    report_lives,
    watch_lives,
)
from probe_build_worker_k3d_manifests import (
    POSTGRES_PINNED,
    PRODUCTION_IMAGE,
    REDIS_PINNED,
    WORKER_CONTAINER,
    applied_documents,
    namespace_document,
    postgres_documents,
    prove_render,
    redis_documents,
    throwaway_secrets,
    web_documents,
)
from probe_build_worker_k3d_web import (
    apply_secrets,
    print_web_logs,
    print_worker_logs,
    web_cells,
    web_selector,
)
from probe_build_worker_starts import (
    CHECK_ARGUMENT,
    REFUSALS,
    RESIDUE_SOURCE,
)

from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import BUILD_IDENTITY, KEY_MOUNT, Predicate

TWIN_POD = "build-worker-twin"
RESIDUE_POD_PREFIX = "build-worker-residue-"
# The planter's residue that plants nothing.
CONTROL = "nothing"
ENDED_PHASES = frozenset({"Succeeded", "Failed"})
# The environment names come from python: `env` would print the later lines of
# a multi-line value.
TWIN_SCRIPT = rf"""cat /proc/self/mountinfo /proc/self/status /proc/self/limits
cat /sys/fs/cgroup/memory.max
id
stat --format '%n %U:%G %a' /etc/hosts /dev/pts /sys/fs/cgroup {KEY_MOUNT}
python -c 'import os; print("\n".join(sorted(os.environ)))'"""


def pod_of(name: str, documents: list[Document]) -> Document:
    """A Pod of the documents' worker pod spec that never restarts."""
    workers = [d for d in documents if d["kind"] == "StatefulSet"]
    spec = copy.deepcopy(workers[0]["spec"]["template"]["spec"])
    spec["restartPolicy"] = "Never"
    metadata = {"name": name, "namespace": NAMESPACE}
    return {"apiVersion": "v1", "kind": "Pod", "metadata": metadata, "spec": spec}


def twin_pod(manifest: str) -> Document:
    """A Pod of the manifest's worker pod spec that prints its facts, not serves."""
    pod = pod_of(TWIN_POD, documents_of(manifest))
    for container in pod["spec"]["containers"]:
        container["command"] = ["sh", "-c", TWIN_SCRIPT]
        container.pop("args", None)
    return pod


def residue_pod_name(residue: str) -> str:
    """The name of the twin that holds the residue."""
    return f"{RESIDUE_POD_PREFIX}{residue}"


def residue_twin(
    worker_documents: list[Document], residue: str, command_tail: list[str]
) -> Document:
    """A Pod of the worker pod spec whose worker starts over the planted residue.

    The planter runs as the container's command with its source as `-c`'s
    argument and execs the template's own command and arguments, then the tail.
    """
    pod = pod_of(residue_pod_name(residue), worker_documents)
    identity = ":".join(map(str, BUILD_IDENTITY))
    for container in pod["spec"]["containers"]:
        if container["name"] == WORKER_CONTAINER:
            start = [*container.get("command", []), *container.pop("args", [])]
            container["command"] = [
                *("python", "-I", "-c", RESIDUE_SOURCE.read_text()),
                residue,
                identity,
                *start,
                *command_tail,
            ]
    return pod


def print_twin(run: Run, manifest: str, title: str) -> None:
    """Print what the manifest's twin Pod prints, then delete the twin."""
    run("kubectl apply --filename -", stdin=json.dumps(twin_pod(manifest)))
    _, finished = await_pod(
        run, TWIN_POD, lambda pod: pod.get("status", {}).get("phase") in ENDED_PHASES
    )
    said = run("kubectl logs", TWIN_POD, *IN_NAMESPACE, check=False)
    verdict = "" if finished else " (did not end)"
    say(f"{title}{verdict}:\n{said.stdout}{said.stderr}")
    run("kubectl delete pod --wait=true", TWIN_POD, *IN_NAMESPACE)


def diagnose(run: Run, manifest: str) -> None:
    """Print the Namespace's events, the node's evidence and the twin Pod's words."""
    sort = "--sort-by=.metadata.creationTimestamp"
    say(f"events:\n{run('kubectl get events', sort, *IN_NAMESPACE).stdout}")
    print_node_evidence(run, sort)
    print_twin(run, manifest, "twin Pod")


def print_node_evidence(run: Run, sort: str) -> None:
    """Print why an image may have left the node: its events, its images, the disk."""
    events = run(
        "kubectl get events --all-namespaces --field-selector",
        "involvedObject.kind=Node",
        sort,
        check=False,
    )
    images = run("docker exec", run.node, "crictl", "images", check=False)
    disk = run("df -h", check=False)
    for title, done in (
        ("node events", events),
        ("node images", images),
        ("runner disk use", disk),
    ):
        say(f"{title}:\n{done.stdout}{done.stderr}")


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


def stop_worker(run: Run) -> None:
    """Delete the StatefulSet and wait for its pod to go; the claim stays."""
    run("kubectl delete statefulset --wait=true", "build-worker", *IN_NAMESPACE)
    run("kubectl wait --for=delete --timeout=60s", f"pod/{WORKER_POD}", *IN_NAMESPACE)


def drive_twin(
    run: Run, documents: list[Document], residue: str, refusal: Predicate | None
) -> tuple[str, bool]:
    """Start the residue's twin, judge its end; give the problem and if it ended."""
    tail = [CHECK_ARGUMENT] if refusal is None else []
    twin = residue_twin(documents, residue, tail)
    name = twin["metadata"]["name"]
    run("kubectl apply --filename -", stdin=json.dumps(twin))
    pod, ended = await_pod(
        run, name, lambda pod: pod.get("status", {}).get("phase") in ENDED_PHASES
    )
    if not ended:
        problem = (
            f"{residue}: the Pod did not end within {TERMINATION_WINDOW_SECONDS} s"
        )
        return problem, False
    life = Life(pod["metadata"]["uid"]).after(pod, deleted=False)
    problem = life_problem(life, Expectation(refusal, init_container=True, lives=1))
    if problem is None:
        status, line = (
            (PASSING_EXIT_STATUS, "self-check passed")
            if refusal is None
            else (REFUSAL_EXIT_STATUS, f"start refused: {refusal}")
        )
        say(f"cell 9: {residue}: exit {status}, {line}")
        return "", True
    say(f"cell 9: {residue}: FAILED, {problem}")
    for termination in life.terminations:
        say(f"cell 9: {residue}: message: {termination.message.strip()[-500:]}")
    return f"{residue}: {problem}", True


def drive_residues(run: Run, overlay: list[Document]) -> list[str]:
    """Cell 9: print the worker pod's facts, then start each twin and delete them."""
    stop_worker(run)
    documents = applied_documents(overlay, init_container=True)
    manifest = as_stream(documents)
    print_twin(run, manifest, "cell 9: the worker pod's facts")
    starts = [(CONTROL, None)] + [
        (residue, predicate)
        for _, predicate, residues in REFUSALS
        for residue in residues
    ]
    problems: list[str] = []
    applied: list[str] = []
    for residue, refusal in starts:
        applied.append(residue_pod_name(residue))
        problem, ended = drive_twin(run, documents, residue, refusal)
        problems.extend(filter(None, [problem]))
        if not ended:
            break
    run("kubectl delete pod --wait=true", *applied, *IN_NAMESPACE)
    if problems:
        diagnose(run, manifest)
    return [f"cell 9: {problem}" for problem in problems]


def drive_web(run: Run, overlay: list[Document]) -> str:
    """Cells 10 to 13: the web without a worker, then two webs over two workers.

    The cells run in order and stop at the first that fails; give its problem.
    """
    documents = web_documents(overlay)
    deployment = next(d for d in documents if d["kind"] == "Deployment")
    worker_documents = applied_documents(overlay, init_container=True, replicas=2)
    manifest = as_stream(worker_documents)
    for store in throwaway_stores(run):
        require_store_in_node(run, store)
    stores = [
        *redis_documents(run.redis_image),
        *postgres_documents(deployment, run.postgres_image),
    ]
    run("kubectl apply --filename -", stdin=as_stream([*stores, *documents]))
    statefulset = next(d for d in worker_documents if d["kind"] == "StatefulSet")
    for number, judge in web_cells(run, deployment, statefulset, manifest):
        if problem := judge():
            say(f"cell {number}: FAILED, {problem}")
            print_web_logs(run, number, web_selector(deployment))
            print_worker_logs(run, number)
            diagnose(run, manifest)
            return f"cell {number}: {problem}"
    return ""


def drive_cells(run: Run, overlay: list[Document]) -> list[str]:
    """Drive the control, its init-container twin, the residues, then the web."""
    deployment = next(d for d in web_documents(overlay) if d["kind"] == "Deployment")
    apply_secrets(run, throwaway_secrets(deployment, run.effects.secret))
    bare = Expectation(Predicate.EGRESS, init_container=False, lives=1)
    control = applied_documents(overlay, init_container=False)
    problems = [drive_cell(run, 7, control, bare)]
    stop_worker(run)
    linked = Expectation(None, init_container=True, lives=LIVES)
    worker = applied_documents(overlay, init_container=True, check=True)
    problems.append(drive_cell(run, 8, worker, linked))
    problems.extend(drive_residues(run, overlay))
    problems.append(drive_web(run, overlay))
    return [problem for problem in problems if problem]


@dataclass(frozen=True)
class ReleasedImage:
    """The image `release.yml` published: its reference and its digest."""

    reference: str
    digest: str

    @property
    def pinned(self) -> str:
        """The production image at the digest, which docker verifies on a pull."""
        return f"{PRODUCTION_IMAGE}@{self.digest}"


RELEASED_IMAGE_FORM = f"{PRODUCTION_IMAGE}:<tag>@sha256:<64 lowercase hex>"
RELEASED_IMAGE = re.compile(
    rf"{re.escape(PRODUCTION_IMAGE)}:[A-Za-z0-9_][A-Za-z0-9_.-]{{0,127}}"
    r"@(?P<digest>sha256:[0-9a-f]{64})"
)


def released_image(reference: str) -> ReleasedImage:
    """The published image of a reference of exactly the registry form; else refuse."""
    matched = RELEASED_IMAGE.fullmatch(reference)
    if matched is None:
        refuse(f"--image must be {RELEASED_IMAGE_FORM}, not {reference!r}")
    return ReleasedImage(reference, matched["digest"])


def use_released_image(run: Run, released: ReleasedImage) -> None:
    """Make the probe tag the published image, proven by its repo digest.

    The pulled reference is removed again whether or not the proof holds.
    """
    pinned = released.pinned
    run("docker pull", pinned)
    try:
        run("docker tag", pinned, run.tag)
        listed = run("docker image inspect --format", "{{json .RepoDigests}}", run.tag)
        digests: list[str] = json.loads(listed.stdout) or []
        if pinned not in digests:
            refuse(f"{run.tag} lacks the repo digest {pinned}; it lists {digests}")
    finally:
        run("docker image rm", pinned, check=False)
    say(f"released image {released.reference} is {run.tag}, repo digest {pinned}")


@dataclass(frozen=True)
class ThrowawayStore:
    """A store the web reaches that the probe runs: its pinned image, its local name."""

    name: str
    pinned: str
    local: str


def throwaway_stores(run: Run) -> tuple[ThrowawayStore, ...]:
    """The probe's Redis and Postgres."""
    return (
        ThrowawayStore("Redis", REDIS_PINNED, run.redis_image),
        ThrowawayStore("Postgres", POSTGRES_PINNED, run.postgres_image),
    )


def pull_store(run: Run, store: ThrowawayStore) -> None:
    """Make the store's local name its pinned image, pulled by the runner.

    The digest in the pulled reference is the proof; the cluster pulls nothing.
    """
    pulled = run("docker pull", store.pinned, check=False)
    if pulled.returncode:
        refuse(
            f"{store.name} image {store.pinned} did not pull: {pulled.stderr[-2000:]}"
        )
    try:
        run("docker tag", store.pinned, store.local)
    finally:
        run("docker image rm", store.pinned, check=False)


def require_store_in_node(run: Run, store: ThrowawayStore) -> None:
    """Refuse unless the node's runtime holds the store's image under its local name.

    The store's pod never pulls, so a missing image would only show as a web
    that never turns Ready; the runtime resolves the name the way the kubelet
    does. The node is asked right after the import, and again before the web
    cells apply the pod: the cluster starts with image garbage collection and
    eviction held off, and this look proves it held.
    """
    found = run("docker exec", run.node, "crictl", "inspecti", store.local, check=False)
    if found.returncode:
        refuse(
            f"{store.name} image {store.local} is not in the cluster's node "
            f"{run.node}: {found.stderr[-2000:]}"
        )


def import_store(run: Run, store: ThrowawayStore) -> None:
    """Put the store's image into the cluster; teardown removes the local name."""
    imported = run("k3d image import", store.local, "--cluster", run.name, check=False)
    if imported.returncode:
        refuse(
            f"{store.name} image {store.local} did not import: "
            f"{imported.stderr[-2000:]}"
        )
    require_store_in_node(run, store)


def drive(run: Run, released: ReleasedImage | None = None) -> None:
    """Drive the cells of the pod shape; always remove what the run made.

    The image is built from the checkout, or is the published one if `released`.
    """
    k3s_version = preflight(run)
    # mkdir refuses an existing path, so a directory planted at the name stops
    # the run instead of holding its kubeconfig.
    run.directory.mkdir(mode=0o700)
    try:
        overlay = prove_render(run)
        if released is None:
            run("docker build --tag", run.tag, str(run.repository))
        else:
            use_released_image(run, released)
        for store in throwaway_stores(run):
            pull_store(run, store)
        start_cluster(run, k3s_version)
        prove_label_filter(run)
        run("k3d image import", run.tag, "--cluster", run.name)
        for store in throwaway_stores(run):
            import_store(run, store)
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


def hold_signals() -> None:
    """Ignore SIGTERM and SIGINT; only SIGKILL reaches the children that inherit it."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def stop_on_signal(
    runner: SubprocessRunner, hold: Callable[[], None]
) -> Callable[[int, object], None]:
    """The handler of SIGTERM and SIGINT: hold signals, kill the children, exit."""

    def stop(signal_number: int, _frame: object) -> None:
        hold()
        runner.kill_children()
        sys.exit(128 + signal_number)

    return stop


def drive_for_real(checkout: Path, released: ReleasedImage | None) -> None:
    """Drive in this process; SIGTERM and SIGINT kill the children, then tear down."""
    runner = SubprocessRunner()
    stop = stop_on_signal(runner, hold_signals)
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
        hold_signals,
        lambda: secrets.token_hex(32),
    )
    identifier = uuid.uuid4().hex[:8]
    drive(
        Run(
            identifier=identifier,
            directory=Path(tempfile.gettempdir()) / f"probe-k3d-{identifier}",
            repository=REPOSITORY,
            platform=checkout.resolve(),
            effects=effects,
        ),
        released,
    )


def process_listing(words: str) -> str:
    """What the fixed `words` print here; refuse if they fail."""
    done = SubprocessRunner()(tuple(words.split()), os.environ, None)
    if done.returncode:
        refuse(f"{words} ended {done.returncode}: {done.stderr[-2000:]}")
    return done.stdout


def check_leftovers(command: Callable[[str], str]) -> None:
    """Print what `leftovers` finds and end 1 if anything is listed."""
    lines = leftovers(command).lines()
    for line in lines:
        say(line)
    if lines:
        sys.exit(1)
    say("no cluster, container, volume or image is left")


def main(
    arguments: list[str],
    drive_checkout: Callable[[Path, ReleasedImage | None], None] = drive_for_real,
) -> None:
    """Run the subcommand the arguments name, or exit with the usage."""
    match arguments:
        case ["pin"]:
            say(PLATFORM_COMMIT)
        case ["leftovers"]:
            check_leftovers(process_listing)
        case ["drive", "--platform", checkout]:
            drive_checkout(Path(checkout), None)
        case ["drive", "--platform", checkout, "--image", reference]:
            drive_checkout(Path(checkout), released_image(reference))
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
