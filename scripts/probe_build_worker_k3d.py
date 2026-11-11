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

import copy
import json
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
    PROBE_REPOSITORY,
    Document,
    Effects,
    Run,
    SubprocessRunner,
    as_stream,
    documents_of,
    preflight,
    start_cluster,
    teardown,
)
from probe_build_worker_k3d_lives import (
    LIVES,
    TERMINATION_WINDOW_SECONDS,
    WORKER_POD,
    Expectation,
    interval_problem,
    lives_problem,
    report_lives,
    watch_lives,
)
from probe_build_worker_k3d_manifests import (
    applied_documents,
    namespace_document,
    prove_render,
)

from presentator.host.worker_self_check import Predicate

TWIN_POD = "build-worker-twin"
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
