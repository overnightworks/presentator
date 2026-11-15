"""The k3d probe's web phase: cells 10 to 13.

Cell 10 starts the web with no worker anywhere and expects it Ready, and a
build in it to end as an outage. Cell 11 applies the workers' Service and two
worker pods, runs two web pods and builds hello-deck in each through the one
Service: the first build's two steps land on two different pods, so the second
step reaches the idle worker while the first pod restarts. Cell 12 sends the
workers a hello under a wrong key and one that reflects the worker's own
direction, and expects both refused and no pod spent. Cell 13 shows, from inside
a worker pod, that the build and wire users reach nothing off the loopback.
"""

import json
from collections.abc import Callable

from probe_build_worker_harness import refuse, say
from probe_build_worker_k3d_builds import (
    build_outcome,
    copy_deck,
    printed_line,
    run_program,
)
from probe_build_worker_k3d_cluster import IN_NAMESPACE, Completed, Document, Run
from probe_build_worker_k3d_lives import (
    TERMINATION_WINDOW_SECONDS,
    WORKER_SELECTOR,
    poll,
)
from probe_build_worker_k3d_manifests import WEB_CONTAINER, WORKER_CONTAINER
from probe_build_worker_web import (
    EGRESS_PROGRAM,
    HELLO_PROGRAM,
    delivers_the_deck,
    is_an_outage,
)

WEB_PODS = 2
WORKER_PODS = 2
# Long enough for a pod that a spent life made terminal to show it.
SETTLE_SECONDS = 5
HELLOS = {"wrong key": False, "reflected": False, "true": True}
LOOPBACK = "loopback"
CONNECTED = "connected"
DROPPED = "EPERM"


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


def web_logs(run: Run, selector: str, *options: str) -> Completed:
    """What `kubectl logs` gives for the web's Pods."""
    return run(
        "kubectl logs --selector",
        selector,
        *("--tail=-1", *options, *IN_NAMESPACE),
        check=False,
    )


def print_web_logs(run: Run, number: int, selector: str) -> None:
    """Print the end of the web's log and of its previous life's."""
    for title, options in (("log", ()), ("previous log", ("--previous",))):
        answer = web_logs(run, selector, *options)
        tail = (answer.stdout + answer.stderr)[-2000:]
        say(f"cell {number}: the web's {title}:\n{tail}")


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


def a_build_problem(run: Run, deployment: Document, web: Document) -> str | None:
    """Why the web pod's build of hello-deck did not deliver it."""
    name = web["metadata"]["name"]
    outcome = build_outcome(run, deployment, name)
    if isinstance(outcome, str):
        return outcome
    return None if delivers_the_deck(outcome) else f"{name} did not deliver: {outcome}"


def built_over_two_workers(
    run: Run, deployment: Document, selector: str, workers: str
) -> str | None:
    """Cell 11: two web pods build through one Service over two worker pods."""
    webs, problem = two_ready_pods(run, selector, workers)
    if problem:
        return problem
    first_workers = uids_of(list_pods(run, WORKER_SELECTOR))
    first, second = webs
    problem = a_build_problem(run, deployment, first)
    if problem:
        return problem
    # One life ends with each step: both pods replaced means the second step
    # reached the idle one while the first restarted.
    _, replaced = await_ready(
        run, WORKER_SELECTOR, WORKER_PODS, apart_from=first_workers
    )
    if not replaced:
        return "the first build's two steps did not take both worker pods"
    problem = copy_deck(run, deployment, second["metadata"]["name"]) or a_build_problem(
        run, deployment, second
    )
    if problem:
        return problem
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


def egress_problem(ends: dict[str, dict[str, str]]) -> str | None:
    """Why a user's attempts did not end at the loopback and nowhere else."""
    for user, attempts in ends.items():
        loopback = attempts.get(LOOPBACK)
        if loopback != CONNECTED:
            return f"the {user} user's connection to the loopback ended {loopback}"
        reached = {
            target: end
            for target, end in attempts.items()
            if target != LOOPBACK and end != DROPPED
        }
        if reached:
            return f"the {user} user's new sockets off the loopback ended {reached}"
    return None


def reaches_only_the_loopback(
    run: Run, selector: str, web_port: int, worker_port: int
) -> str | None:
    """Cell 13: the build and wire users' new sockets cannot leave the loopback."""
    webs, ready = await_ready(run, selector, WEB_PODS)
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
    ends: dict[str, dict[str, str]] = {
        user: attempts for user, attempts in said.items() if isinstance(attempts, dict)
    }
    if problem := egress_problem(ends):
        return problem
    say(
        f"cell 13: the build and wire users reached the loopback and got {DROPPED} "
        f"to {', '.join(targets)}; the replies of the accepted connections passed "
        f"in cell 11's builds"
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
        (13, lambda: reaches_only_the_loopback(run, selector, web_port, worker_port)),
    ]
