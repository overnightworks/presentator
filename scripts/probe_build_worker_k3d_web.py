"""The k3d probe's web phase: cells 10 to 13.

Cell 10 starts the web with no worker anywhere and expects it Ready, and a
build in it to end as an outage. Cell 11 applies the workers' Service and two
worker pods, runs two web pods and builds hello-deck in each through the one
Service: the first build's two steps land on two different pods, and the second
step is admitted while the first pod is not Ready, proven by a sample of the
pods taken at or after the admission, so it reaches the idle worker while the
first restarts. Cell 12 sends the
workers a hello under a wrong key and one that reflects the worker's own
direction, and expects both refused and no pod spent. Cell 13 shows, from inside
a worker pod, that the build and wire users reach nothing off the loopback, and,
from a web pod, that a port of the worker pod other than the worker's own drops
what reaches it although a process listens there and a policy of the probe's own
admits the web's pods to every port.
"""

import contextlib
import json
from collections.abc import Callable, Generator, Iterator
from typing import cast

from probe_build_worker_harness import refuse, say
from probe_build_worker_k3d_builds import (
    CLIENT_SECONDS,
    build_outcome,
    copy_deck,
    printed_line,
    run_program,
)
from probe_build_worker_k3d_cluster import (
    IN_NAMESPACE,
    NAMESPACE,
    Completed,
    Document,
    Run,
)
from probe_build_worker_k3d_lives import (
    TERMINATION_WINDOW_SECONDS,
    WORKER_SELECTOR,
    poll,
)
from probe_build_worker_k3d_manifests import (
    INBOUND_PROBE_POLICY,
    WEB_CONTAINER,
    WORKER_CONTAINER,
    inbound_probe_policy,
)
from probe_build_worker_web import (
    EGRESS_PROGRAM,
    EGRESS_USERS,
    HELLO_PROGRAM,
    INBOUND_PROGRAM,
    Outcome,
    delivers_the_deck,
    is_an_outage,
)

WEB_PODS = 2
WORKER_PODS = 2
# Long enough for a pod that a spent life made terminal to show it.
SETTLE_SECONDS = 5
# Long enough for the cluster's policy controller to program an applied policy.
POLICY_SETTLE_SECONDS = 15
HELLOS = {"wrong key": False, "reflected": False, "true": True}
READY = "True"
READY_STATUSES = (
    """'{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{" "}{end}'"""
)
LOOPBACK = "loopback"
POD_LOOPBACK = "127.0.0.1"
CONNECTED = "connected"
DROPPED = "EPERM"
TIMED_OUT = "timeout"


def web_selector(deployment: Document) -> str:
    """The Deployment's `spec.selector.matchLabels` as a label selector."""
    labels = deployment["spec"]["selector"]["matchLabels"]
    return ",".join(f"{key}={value}" for key, value in labels.items())


def apply_secrets(run: Run, secrets: list[Document]) -> None:
    """Apply the Secrets from stdin; a failure names only the exit status.

    The command's stderr is not quoted: a rejected object may be echoed in it.
    """
    stream = "\n---\n".join(json.dumps(secret) for secret in secrets)
    done = run("kubectl apply --filename -", stdin=stream, check=False)
    if done.returncode:
        refuse(f"applying the Secrets ended {done.returncode}")


def list_pods(run: Run, selector: str) -> list[Document]:
    """The Pods the selector names, oldest first; none if kubectl cannot list them."""
    answer = run(
        "kubectl get pods --output json",
        *("--selector", selector, *IN_NAMESPACE),
        check=False,
    )
    items: list[Document] = (
        json.loads(answer.stdout)["items"] if answer.returncode == 0 else []
    )
    return sorted(
        items,
        key=lambda pod: (pod["metadata"]["creationTimestamp"], pod["metadata"]["name"]),
    )


def is_ready(pod: Document) -> bool:
    """Whether the Pod's Ready condition is True."""
    conditions = pod.get("status", {}).get("conditions", [])
    return any(c["type"] == "Ready" and c["status"] == "True" for c in conditions)


def uids_of(pods: list[Document]) -> frozenset[str]:
    """The uids of the Pods."""
    return frozenset(pod["metadata"]["uid"] for pod in pods)


def await_ready(
    run: Run, selector: str, count: int, *, apart_from: frozenset[str] = frozenset()
) -> tuple[list[Document], bool]:
    """Poll until `count` Pods are listed, all Ready, none a uid of `apart_from`."""

    def settled(pods: list[Document]) -> bool:
        return len(pods) == count and all(
            is_ready(pod) and pod["metadata"]["uid"] not in apart_from for pod in pods
        )

    return poll(run, lambda: list_pods(run, selector), settled, [])


def web_restarts(web: Document) -> int:
    """The web container's restartCount."""
    statuses = web.get("status", {}).get("containerStatuses", [])
    return next((s["restartCount"] for s in statuses if s["name"] == WEB_CONTAINER), 0)


def pod_logs(run: Run, selector: str, *options: str) -> Completed:
    """What `kubectl logs` gives for the Pods the selector names."""
    return run(
        "kubectl logs --selector",
        selector,
        *("--tail=-1", *options, *IN_NAMESPACE),
        check=False,
    )


def print_web_logs(run: Run, number: int, selector: str) -> None:
    """Print the end of the web's log and of its previous life's."""
    for title, options in (("log", ()), ("previous log", ("--previous",))):
        answer = pod_logs(run, selector, *options)
        tail = (answer.stdout + answer.stderr)[-2000:]
        say(f"cell {number}: the web's {title}:\n{tail}")


def print_worker_logs(run: Run, number: int) -> None:
    """Print the end of the log of each worker Pod that exists."""
    answer = pod_logs(run, WORKER_SELECTOR, "--prefix", "--container", WORKER_CONTAINER)
    say(f"cell {number}: the workers' log:\n{(answer.stdout + answer.stderr)[-2000:]}")


def container_port(workload: Document, container: str) -> int:
    """The first port the workload's container of that name declares."""
    spec = workload["spec"]["template"]["spec"]
    named = next(c for c in spec["containers"] if c["name"] == container)
    return named["ports"][0]["containerPort"]


def web_without_workers(run: Run, selector: str) -> tuple[str | None, str]:
    """Await the one web pod with no worker anywhere; give the problem and its name."""
    pods, ready = await_ready(run, selector, 1)
    if not ready:
        return f"the web was not Ready within {TERMINATION_WINDOW_SECONDS} s", ""
    (web,) = pods
    problem = None
    if web_restarts(web):
        problem = f"the web's restartCount is {web_restarts(web)}, expected 0"
    elif list_pods(run, WORKER_SELECTOR):
        problem = "a worker pod exists before the workers were applied"
    return problem, web["metadata"]["name"]


def unavailable_without_a_worker(
    run: Run, deployment: Document, selector: str
) -> str | None:
    """Cell 10: the web is Ready with no worker, and a build there is an outage."""
    problem, name = web_without_workers(run, selector)
    if problem:
        return problem
    say("cell 10: the web is Ready with no worker pod and no Service, restarts 0")
    outcome = copy_deck(run, deployment, name) or build_outcome(run, deployment, name)
    if isinstance(outcome, str):
        return outcome
    if not is_an_outage(outcome):
        return f"a build that found no worker did not end as an outage: {outcome}"
    say(
        "cell 10: a build that finds no worker fails as an outage, its step unavailable"
    )
    return None


def two_ready_pods(
    run: Run, selector: str, workers: str
) -> tuple[list[Document], str | None]:
    """Apply the workers, run two web pods; give them once both kinds are Ready."""
    run("kubectl apply --filename -", stdin=workers)
    run("kubectl scale deployment web", "--replicas", str(WEB_PODS), *IN_NAMESPACE)
    webs, web_ready = await_ready(run, selector, WEB_PODS)
    _, worker_ready = await_ready(run, WORKER_SELECTOR, WORKER_PODS)
    if web_ready and worker_ready:
        return webs, None
    waited = f"within {TERMINATION_WINDOW_SECONDS} s"
    return webs, f"two web pods and two worker pods were not Ready {waited}"


def delivering_outcome(run: Run, deployment: Document, web: Document) -> Outcome | str:
    """The outcome of the web pod's build of hello-deck, or why it did not deliver."""
    name = web["metadata"]["name"]
    outcome = build_outcome(run, deployment, name)
    if isinstance(outcome, str) or delivers_the_deck(outcome):
        return outcome
    return f"{name} did not deliver: {outcome}"


def sampled_while_building(run: Run) -> Generator[str]:
    """Stamped, readied samples of the worker pods that run beside a build.

    Each line is the host's epoch time, taken before kubectl reads the pods, then
    the Ready status of each worker pod listed: the reading is no older than the
    stamp, and a failed reading prints nothing.
    """
    read = (
        f"kubectl get pods --selector {WORKER_SELECTOR} --namespace {NAMESPACE} "
        f"--output jsonpath={READY_STATUSES}"
    )
    loop = (
        f"while :; do now=$(date +%s.%N); ready=$({read}) || continue; "
        'echo "$now $ready"; sleep 0.1; done'
    )
    return run.watch(
        f"timeout {CLIENT_SECONDS + TERMINATION_WINDOW_SECONDS} sh -c", loop
    )


def restart_overlap_problem(
    samples: Iterator[str], admitted: float | None
) -> str | None:
    """Why the second step was not admitted while a worker restarted.

    The first sample taken at or after the admission, on the host's clock that the
    web pod shares, must show a worker pod not Ready: the Ready condition's own
    time is cut to the second, a sample's stamp is not.
    """
    if admitted is None:
        return "the second step was never admitted by a worker"
    for line in samples:
        stamp, *statuses = line.split()
        if float(stamp) >= admitted:
            if statuses == [READY] * WORKER_PODS:
                return (
                    f"the second step was admitted after the first worker was "
                    f"Ready again (admitted at {admitted}, both Ready at {stamp})"
                )
            return None
    return "no sample of the worker pods was taken after the second step's admission"


def first_build_problem(run: Run, deployment: Document, first: Document) -> str | None:
    """Why the first build did not take both workers, the second while one restarted."""
    first_workers = uids_of(list_pods(run, WORKER_SELECTOR))
    with contextlib.closing(sampled_while_building(run)) as samples:
        next(samples, None)
        outcome = delivering_outcome(run, deployment, first)
        if isinstance(outcome, str):
            return outcome
        # One life ends with each step: both pods replaced means the second step
        # reached the idle one.
        _, replaced = await_ready(
            run, WORKER_SELECTOR, WORKER_PODS, apart_from=first_workers
        )
        if not replaced:
            return "the first build's two steps did not take both worker pods"
        return restart_overlap_problem(samples, outcome.steps[1].admitted)


def built_over_two_workers(
    run: Run, deployment: Document, selector: str, workers: str
) -> str | None:
    """Cell 11: two web pods build through one Service over two worker pods."""
    webs, problem = two_ready_pods(run, selector, workers)
    if problem:
        return problem
    first, second = webs
    if problem := first_build_problem(run, deployment, first):
        return problem
    if problem := copy_deck(run, deployment, second["metadata"]["name"]):
        return problem
    if isinstance(outcome := delivering_outcome(run, deployment, second), str):
        return outcome
    say(
        f"cell 11: {first['metadata']['name']} and {second['metadata']['name']} built "
        f"and exported hello-deck through one Service; the first build's two steps "
        f"took both worker pods, the second step the idle one while the first restarted"
    )
    return None


def hellos_refused_unspent(run: Run, selector: str) -> str | None:
    """Cell 12: a wrong-key and a reflected hello are refused and spend no life."""
    webs, ready = await_ready(run, selector, WEB_PODS)
    workers, settled = await_ready(run, WORKER_SELECTOR, WORKER_PODS)
    if not (ready and settled):
        return f"the pods were not Ready within {TERMINATION_WINDOW_SECONDS} s"
    web = webs[0]
    done = run_program(run, web["metadata"]["name"], WEB_CONTAINER, HELLO_PROGRAM)
    said = printed_line(done)
    if isinstance(said, str):
        return said
    if said != HELLOS:
        return f"the workers proved themselves to {said}, expected {HELLOS}"
    run.effects.sleep(SETTLE_SECONDS)
    after, held = await_ready(run, WORKER_SELECTOR, WORKER_PODS)
    if not held or uids_of(after) != uids_of(workers):
        return "the hellos spent a worker's life: its pod was replaced"
    say(
        "cell 12: a wrong-key hello and a reflected hello were refused, a true "
        "hello was served, and both worker pods kept their uids and stayed Ready"
    )
    return None


def attempts_of(said: dict[str, object], user: str) -> dict[str, str] | None:
    """How the user's attempts ended, or None if the probe printed none of them."""
    attempts = said.get(user)
    if not isinstance(attempts, dict):
        return None
    return {
        str(target): str(end)
        for target, end in cast("dict[object, object]", attempts).items()
    }


def egress_problem(said: dict[str, object], targets: tuple[str, ...]) -> str | None:
    """Why the users' attempts did not end at the loopback and nowhere else.

    Both users must have tried the loopback and every target, so a probe that
    printed less proves nothing.
    """
    for user in EGRESS_USERS:
        attempts = attempts_of(said, user)
        if attempts is None:
            return f"the egress probe printed no attempts of the {user} user"
        if missing := [name for name in (LOOPBACK, *targets) if name not in attempts]:
            return f"the {user} user's attempts lack {missing}"
        if (loopback := attempts[LOOPBACK]) != CONNECTED:
            return f"the {user} user's connection to the loopback ended {loopback}"
        reached = {t: attempts[t] for t in targets if attempts[t] != DROPPED}
        if reached:
            return f"the {user} user's new sockets off the loopback ended {reached}"
    return None


def reach_end(run: Run, pod: Document, container: str, target: str) -> str:
    """How a new connection from the pod's container to HOST:PORT ended, or why not."""
    said = printed_line(
        run_program(
            run, pod["metadata"]["name"], container, INBOUND_PROGRAM, "reach", target
        )
    )
    return said if isinstance(said, str) else str(said.get("end"))


def inbound_problem(
    run: Run, deployment: Document, web: Document, worker: Document
) -> str | None:
    """Why a web pod's connection to a listener in the worker pod was not dropped.

    The listener is on a port other than the worker's own. The workers'
    NetworkPolicy drops such a connection too, so a policy of the probe's own
    admits the web's pods to every port while the connection is tried: only the
    pod's packet filter can drop it then. The listener must answer on the pod's
    loopback, or its timeout would say nothing.
    """
    listening = printed_line(
        run_program(
            run, worker["metadata"]["name"], WORKER_CONTAINER, INBOUND_PROGRAM, "listen"
        )
    )
    if isinstance(listening, str):
        return listening
    port = listening.get("port")
    if (own := reach_end(run, worker, WORKER_CONTAINER, f"{POD_LOOPBACK}:{port}")) != (
        CONNECTED
    ):
        return f"the listener in the worker pod did not answer on its loopback: {own}"
    target = f"{worker['status']['podIP']}:{port}"
    run(
        "kubectl apply --filename -", stdin=json.dumps(inbound_probe_policy(deployment))
    )
    try:
        run.effects.sleep(POLICY_SETTLE_SECONDS)
        end = reach_end(run, web, WEB_CONTAINER, target)
    finally:
        run(
            "kubectl delete networkpolicy --ignore-not-found",
            INBOUND_PROBE_POLICY,
            *IN_NAMESPACE,
            check=False,
        )
    if end != TIMED_OUT:
        return f"a web pod's connection to {target} ended {end}"
    return None


def reaches_only_the_loopback(
    run: Run, deployment: Document, web_port: int, worker_port: int
) -> str | None:
    """Cell 13: the build and wire users stay on the loopback, no other port is open."""
    webs, ready = await_ready(run, web_selector(deployment), WEB_PODS)
    workers, settled = await_ready(run, WORKER_SELECTOR, WORKER_PODS)
    if not (ready and settled):
        return f"the pods were not Ready within {TERMINATION_WINDOW_SECONDS} s"
    web, (worker, other) = webs[0], workers
    targets = (
        f"{web['status']['podIP']}:{web_port}",
        f"{other['status']['podIP']}:{worker_port}",
    )
    said = printed_line(
        run_program(
            run, worker["metadata"]["name"], WORKER_CONTAINER, EGRESS_PROGRAM, *targets
        )
    )
    if isinstance(said, str):
        return said
    problem = egress_problem(said, targets) or inbound_problem(
        run, deployment, web, worker
    )
    if problem:
        return problem
    say(
        f"cell 13: the build and wire users reached the loopback and got {DROPPED} "
        f"to {', '.join(targets)}; the replies of the accepted connections passed "
        f"in cell 11's builds; with every port of the worker pod admitted by "
        f"policy, a web pod's connection to a port where a process listens, "
        f"other than the worker's, ended in a {TIMED_OUT}"
    )
    return None


def web_cells(
    run: Run, deployment: Document, workers: Document, worker_documents: str
) -> list[tuple[int, Callable[[], str | None]]]:
    """The cells of the web phase, each as its number and its judgement."""
    selector = web_selector(deployment)
    web_port = container_port(deployment, WEB_CONTAINER)
    worker_port = container_port(workers, WORKER_CONTAINER)
    return [
        (10, lambda: unavailable_without_a_worker(run, deployment, selector)),
        (
            11,
            lambda: built_over_two_workers(run, deployment, selector, worker_documents),
        ),
        (12, lambda: hellos_refused_unspent(run, selector)),
        (13, lambda: reaches_only_the_loopback(run, deployment, web_port, worker_port)),
    ]
