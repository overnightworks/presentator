r"""Drive the build worker's pod shape in an ordinary k3d cluster.

Usage: probe_build_worker_k3d.py pin
       probe_build_worker_k3d.py leftovers
       probe_build_worker_k3d.py drive --platform CHECKOUT

`pin` prints the one platform commit this probe is written against; CI checks
platform out at it. `leftovers` lists the k3d clusters, the containers and
volumes labelled `app=k3d` and the images of the probe and of production,
prints each and ends 1 if any is listed; it needs no cluster, run directory or
kubeconfig, and CI runs it after each drive. `drive` refuses unless CHECKOUT is at
that commit and k3d, kubectl and docker are on PATH, proves that the overlay
rendering the probe image differs from platform's own render only in the
production image fields of every pod template, then drives three cells in a
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
teardown is bounded by the wrapper's timeout. CI runs it on a clean runner; a local run
takes the probe-stack lock:

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
import uuid
from collections.abc import Callable
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
    interval_problem,
    life_problem,
    lives_problem,
    report_lives,
    watch_lives,
)
from probe_build_worker_k3d_manifests import (
    WORKER_CONTAINER,
    applied_documents,
    namespace_document,
    prove_render,
)
from probe_build_worker_starts import (
    CHECK_ARGUMENT,
    REFUSALS,
    RESIDUE_SOURCE,
)

from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import BUILD_IDENTITY, Predicate

TWIN_POD = "build-worker-twin"
RESIDUE_POD_PREFIX = "build-worker-residue-"
# The planter's residue that plants nothing.
CONTROL = "nothing"
ENDED_PHASES = frozenset({"Succeeded", "Failed"})
POLL_SECONDS = 2
# The environment names come from python: `env` would print the later lines of
# a multi-line value.
TWIN_SCRIPT = r"""cat /proc/self/mountinfo /proc/self/status /proc/self/limits
cat /sys/fs/cgroup/memory.max
id
stat --format '%n %U:%G %a' /etc/hosts /dev/pts /sys/fs/cgroup
python -c 'import os; print("\n".join(sorted(os.environ)))'"""


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


def drive_cells(run: Run, overlay: list[Document]) -> list[str]:
    """Drive the control, its twin with the init container, then the residues."""
    bare = Expectation(Predicate.NO_ROUTE, init_container=False, lives=1)
    control = applied_documents(overlay, init_container=False)
    problems = [drive_cell(run, 7, control, bare)]
    stop_worker(run)
    linked = Expectation(None, init_container=True, lives=LIVES)
    worker = applied_documents(overlay, init_container=True, check=True)
    problems.append(drive_cell(run, 8, worker, linked))
    return [problem for problem in problems if problem] + drive_residues(run, overlay)


def drive(run: Run) -> None:
    """Drive the cells of the pod shape; always remove what the run made."""
    k3s_version = preflight(run)
    # mkdir refuses an existing path, so a directory planted at the name stops
    # the run instead of holding its kubeconfig.
    run.directory.mkdir(mode=0o700)
    try:
        overlay = prove_render(run)
        run("docker build --tag", run.tag, str(run.repository))
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


def drive_for_real(checkout: Path) -> None:
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


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["pin"]:
            say(PLATFORM_COMMIT)
        case ["leftovers"]:
            check_leftovers(process_listing)
        case ["drive", "--platform", checkout]:
            drive_for_real(Path(checkout))
        case _:
            sys.exit(__doc__)
