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
PersistentVolumeClaims, ServiceAccounts and worker StatefulSet of
`apps/presentator`; the applied copy differs from the render only in the worker
container's `terminationMessagePolicy` (so a refusal reaches the terminated
state's message), in cell 7 in the deleted init container, and in cell 8 in the
worker container's `args`, which are `--check`.

Platform replaces a used pod and its log with it, so cells 7 and 8 judge their
lives from one bounded watch of the worker pod. Cell 7 reads one life without
init container and expects the self-check to refuse with no-route. Cell 8 reads
LIVES lives, each passing the whole self-check under `--check`, and expects,
per life, a new uid, remove-link ending 0, one termination of the worker with
exit 0 and restartCount 0; it prints each interval from a life's end to its
successor's start, which must stay below the web's HELLO retry window.

Cell 9 runs after cell 8, whether or not it failed, on the StatefulSet's pod
spec of cell 8 with the StatefulSet deleted and its pod gone. It prints the
worker pod's facts (mountinfo, status, limits and the rest of the twin's
script) from one bare Pod, then starts bare Pods one at a time: the
control `nothing`, which plants no residue and ends 0 with the self-check
passed under `--check`, and one twin per residue of the Compose proof's
REFUSALS. Each twin plants its residue with the planter, passed as
`python -I -c`, and becomes the worker; it must end with exit 2 and refuse with
the predicate its residue belongs to, with remove-link 0 and restartCount 0,
judged from its terminated state. A twin that does not end within the
termination window fails the cell and no later twin starts. Every twin is
deleted before the cell returns.

Cell 10 runs after cell 9 and starts the web as platform renders it, at the
pin: the render's ConfigMap and Deployment, applied unaltered, and a Secret the
driver generates. The Secret's name and keys come from the Deployment's
`secrets` volume and the files its environment names under that volume's mount,
and each value comes from the effect `secret`, 64 hex characters from `secrets`
in a real run. The values go to `kubectl apply` on stdin only and never to a
printed line, an error, an argument or a file; they live in the run's cluster.
The cell awaits the web's log with the text its composition root logs while it
waits for the worker, reads the Pod once (not Ready, its container not
terminated), then applies the worker's documents as cell 8 does but without
`--check`. It awaits the web's Ready condition and judges the web's restartCount
(0), the worker pod's remove-link (exit 0) and that Ready came after the worker
container started. A failure prints the web's log and its previous life's.

Cell 11 runs after cell 10 passed, with the web Ready and the worker serving:
it copies `examples/hello-deck` into the web pod's `emptyDir` with `kubectl cp`
and runs the web's own client there with `kubectl exec`, so the deck and the
client cross no network but the run's own API server. The web's composition
builds and exports the deck through the worker, whose life ends with each step;
the client reads the talk's page and the PDF in the web pod. The cell is judged
by the client's outcome and by a new worker pod with remove-link 0 and restarts 0.

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
takes the probe-stack lock:

    flock -o -w 3600 /tmp/probe-stack.lock setpriv --pdeathsig TERM \
        timeout -k 30 1800 python scripts/probe_build_worker_k3d.py drive \
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
from datetime import datetime
from pathlib import Path

from probe_build_worker_harness import REPOSITORY, refuse, say
from probe_build_worker_k3d_builds import drive_build
from probe_build_worker_k3d_cluster import (
    IN_NAMESPACE,
    NAMESPACE,
    PLATFORM_COMMIT,
    Completed,
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
    container_status,
    interval_problem,
    life_problem,
    lives_problem,
    poll,
    read_pod,
    remove_link_problem,
    report_lives,
    watch_lives,
)
from probe_build_worker_k3d_manifests import (
    PRODUCTION_IMAGE,
    WEB_CONTAINER,
    WORKER_CONTAINER,
    applied_documents,
    namespace_document,
    prove_render,
    throwaway_secret,
    web_documents,
)
from probe_build_worker_starts import (
    CHECK_ARGUMENT,
    REFUSALS,
    RESIDUE_SOURCE,
)

from presentator.host.main import WAITING_FOR_THE_WORKER
from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import BUILD_IDENTITY, Predicate

TWIN_POD = "build-worker-twin"
RESIDUE_POD_PREFIX = "build-worker-residue-"
# The planter's residue that plants nothing.
CONTROL = "nothing"
ENDED_PHASES = frozenset({"Succeeded", "Failed"})
# The environment names come from python: `env` would print the later lines of
# a multi-line value.
TWIN_SCRIPT = r"""cat /proc/self/mountinfo /proc/self/status /proc/self/limits
cat /sys/fs/cgroup/memory.max
id
stat --format '%n %U:%G %a' /etc/hosts /dev/pts /sys/fs/cgroup
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
    """Print the Namespace's events and what the manifest's twin Pod prints."""
    sort = "--sort-by=.metadata.creationTimestamp"
    say(f"events:\n{run('kubectl get events', sort, *IN_NAMESPACE).stdout}")
    print_twin(run, manifest, "twin Pod")


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


def web_selector(deployment: Document) -> str:
    """The Deployment's `spec.selector.matchLabels` as a label selector."""
    labels = deployment["spec"]["selector"]["matchLabels"]
    return ",".join(f"{key}={value}" for key, value in labels.items())


def apply_secret(run: Run, secret: Document) -> None:
    """Apply the Secret from stdin; a failure names only the exit status.

    The command's stderr is not quoted: a rejected object may be echoed in it.
    """
    done = run("kubectl apply --filename -", stdin=json.dumps(secret), check=False)
    if done.returncode:
        refuse(f"applying the Secret ended {done.returncode}")


def web_pod(run: Run, selector: str) -> Document | None:
    """The web's Pod, or none while there is no Pod or kubectl cannot list it."""
    answer = run(
        "kubectl get pods --output json",
        *("--selector", selector, *IN_NAMESPACE),
        check=False,
    )
    items: list[Document] = (
        json.loads(answer.stdout)["items"] if answer.returncode == 0 else []
    )
    return items[0] if items else None


def web_logs(run: Run, selector: str, *options: str) -> Completed:
    """What `kubectl logs` gives for the web's Pod."""
    return run(
        "kubectl logs --selector",
        selector,
        *("--tail=-1", *options, *IN_NAMESPACE),
        check=False,
    )


def web_log(run: Run, selector: str) -> str | None:
    """The web's log, or none while kubectl has none to give."""
    answer = web_logs(run, selector)
    return answer.stdout if answer.returncode == 0 else None


def ready_since(pod: Document) -> str | None:
    """When the Pod's Ready condition last turned True; none if it is not Ready."""
    conditions = pod.get("status", {}).get("conditions", [])
    ready = next((c for c in conditions if c["type"] == "Ready"), Document())
    return ready.get("lastTransitionTime") if ready.get("status") == "True" else None


def control_problem(pod: Document | None) -> str | None:
    """Why the web that logged its wait is not waiting, not Ready and alive."""
    if pod is None:
        return "no web Pod could be read after its wait was logged"
    if ready_since(pod):
        return "the web was Ready before the worker started"
    if "terminated" in container_status(pod, WEB_CONTAINER).get("state", {}):
        return "the web's container terminated before the worker started"
    return None


def seconds_after_start(web: Document, worker: Document) -> float | None:
    """How long after the worker container started the web turned Ready."""
    state = container_status(worker, WORKER_CONTAINER).get("state", Document())
    began: Document = state.get("running") or state.get("terminated") or Document()
    started: str | None = began.get("startedAt")
    ready = ready_since(web)
    if started is None or ready is None:
        return None
    return (
        datetime.fromisoformat(ready) - datetime.fromisoformat(started)
    ).total_seconds()


def ready_problem(web: Document, worker: Document | None) -> str | None:
    """Why the Ready web is not one that never refused and followed the worker."""
    restarts = web_restarts(web)
    if restarts:
        return f"the web's restartCount is {restarts}, expected 0"
    if worker is None:
        return f"the worker Pod {WORKER_POD} cannot be read"
    if problem := remove_link_problem(
        Life(worker["metadata"]["uid"]).after(worker, deleted=False)
    ):
        return problem
    after = seconds_after_start(web, worker)
    if after is None:
        return "the worker container has not started"
    if after < 0:
        return f"the web was Ready {-after:g} s before the worker started"
    return None


def web_restarts(web: Document) -> int:
    """The web container's restartCount."""
    return container_status(web, WEB_CONTAINER).get("restartCount", 0)


def report_control(web: Document) -> None:
    """Print what the web's Pod shows while it waits for the worker."""
    (state, *_) = container_status(web, WEB_CONTAINER).get("state", {"unknown": {}})
    say(
        f"cell 10: control: the web logged its wait, Ready False, "
        f"container {state}, restarts {web_restarts(web)}"
    )


def report_web(web: Document, worker: Document) -> None:
    """Print how long after the worker started the web was Ready."""
    life = Life(worker["metadata"]["uid"]).after(worker, deleted=False)
    say(
        f"cell 10: the web waited, then was Ready "
        f"{seconds_after_start(web, worker):g} s after the worker started "
        f"(web restarts {web_restarts(web)}; worker pod {life.uid}, "
        f"{worker['status']['phase']}, restarts {life.restarts})"
    )


def judge_web(run: Run, selector: str, worker: str) -> str | None:
    """Judge the web's wait as the control, start the worker, judge the web's Ready."""
    waiting = WAITING_FOR_THE_WORKER.split("%", 1)[0]
    _, logged = poll(
        run, lambda: web_log(run, selector), lambda log: waiting in log, ""
    )
    if not logged:
        return f"the web did not log its wait within {TERMINATION_WINDOW_SECONDS} s"
    waiting_pod = web_pod(run, selector)
    if problem := control_problem(waiting_pod):
        return problem
    if waiting_pod is not None:
        report_control(waiting_pod)
    run("kubectl apply --filename -", stdin=worker)
    web, ready = poll(
        run, lambda: web_pod(run, selector), lambda pod: bool(ready_since(pod)), {}
    )
    if not ready:
        return f"the web was not Ready within {TERMINATION_WINDOW_SECONDS} s"
    pod = read_pod(run, WORKER_POD)
    problem = ready_problem(web, pod)
    if problem is None and pod is not None:
        report_web(web, pod)
    return problem


def print_web_logs(run: Run, number: int, selector: str) -> None:
    """Print the end of the web's log and of its previous life's."""
    for title, options in (("log", ()), ("previous log", ("--previous",))):
        answer = web_logs(run, selector, *options)
        tail = (answer.stdout + answer.stderr)[-2000:]
        say(f"cell {number}: the web's {title}:\n{tail}")


def build_through_web(run: Run, deployment: Document, selector: str) -> str | None:
    """Cell 11 in the web's Pod; give the problem."""
    pod = web_pod(run, selector)
    if pod is None:
        return "no web Pod could be read to build in"
    return drive_build(run, deployment, pod["metadata"]["name"])


def drive_web(run: Run, overlay: list[Document]) -> str:
    """Cells 10 and 11: the web waits, is Ready once the worker runs, then builds.

    A Secret of generated values goes in first, from stdin only. Cell 11 runs
    only when cell 10 passed; give the problem of the one that failed.
    """
    documents = web_documents(overlay)
    deployment = next(d for d in documents if d["kind"] == "Deployment")
    apply_secret(run, throwaway_secret(deployment, run.effects.secret))
    run("kubectl apply --filename -", stdin=as_stream(documents))
    selector = web_selector(deployment)
    worker = as_stream(applied_documents(overlay, init_container=True))
    number, problem = 10, judge_web(run, selector, worker)
    if problem is None:
        number, problem = 11, build_through_web(run, deployment, selector)
    if problem is None:
        return ""
    say(f"cell {number}: FAILED, {problem}")
    print_web_logs(run, number, selector)
    diagnose(run, worker)
    return f"cell {number}: {problem}"


def drive_cells(run: Run, overlay: list[Document]) -> list[str]:
    """Drive the control, its init-container twin, the residues, then the web."""
    bare = Expectation(Predicate.NO_ROUTE, init_container=False, lives=1)
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
        start_cluster(run, k3s_version)
        prove_label_filter(run)
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


def hold_signals() -> None:
    """Ignore SIGTERM and SIGINT; only SIGKILL reaches the children that inherit it."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def stop_on_signal(
    runner: SubprocessRunner, hold: Callable[[], None]
) -> Callable[[int, object], None]:
    """The handler of SIGTERM and SIGINT: hold further signals, kill the child, exit."""

    def stop(signal_number: int, _frame: object) -> None:
        hold()
        runner.kill_child()
        sys.exit(128 + signal_number)

    return stop


def drive_for_real(checkout: Path, released: ReleasedImage | None) -> None:
    """Drive in this process; SIGTERM and SIGINT kill the child, then tear down."""
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


def main(arguments: list[str]) -> None:
    """Run the subcommand the arguments name, or exit with the usage."""
    match arguments:
        case ["pin"]:
            say(PLATFORM_COMMIT)
        case ["leftovers"]:
            check_leftovers(process_listing)
        case ["drive", "--platform", checkout]:
            drive_for_real(Path(checkout), None)
        case ["drive", "--platform", checkout, "--image", reference]:
            drive_for_real(Path(checkout), released_image(reference))
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
