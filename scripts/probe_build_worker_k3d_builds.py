"""The k3d probe's build through the web pod's own composition (cell 11).

`drive_build` copies `examples/hello-deck` into the web pod's scratch directory
and runs the web's client there, as the container's own user with its
environment: only the web's composition runs, `load_settings`, `the_build_worker`
and the socket. The scratch directory is the web container's own `emptyDir`
mount, read from the rendered Deployment; the builds root belongs to the web's
sweep. The client builds and exports the deck through the worker, and the run
is judged by its outcome and by a worker pod that is not the one the steps
began in, whose remove-link ended 0 and whose worker did not restart. The
worker's life ends with each accepted step, so a build and an export spend two.
"""

from probe_build_worker_harness import say
from probe_build_worker_k3d_cluster import IN_NAMESPACE, Completed, Document, Run
from probe_build_worker_k3d_lives import (
    TERMINATION_WINDOW_SECONDS,
    WORKER_POD,
    Life,
    await_pod,
    container_status,
    read_pod,
    remove_link_problem,
)
from probe_build_worker_k3d_manifests import WEB_CONTAINER, WORKER_CONTAINER
from probe_build_worker_web import (
    CLIENT_PROGRAM,
    HELLO_STEPS,
    WEB_PYTHON,
    Outcome,
    delivers_the_deck,
    outcome_in,
)

HELLO_DECK = "hello-deck"
COPY_SECONDS = 120
CLIENT_SECONDS = 600
TAIL_CHARACTERS = 2000


def scratch_mount(deployment: Document) -> str:
    """The path where the web container mounts its one `emptyDir` volume."""
    pod = deployment["spec"]["template"]["spec"]
    scratch = {volume["name"] for volume in pod["volumes"] if "emptyDir" in volume}
    container = next(c for c in pod["containers"] if c["name"] == WEB_CONTAINER)
    (mount,) = [
        m["mountPath"] for m in container["volumeMounts"] if m["name"] in scratch
    ]
    return mount


def read_outcome(done: Completed) -> Outcome | str:
    """The client's outcome, or what is wrong with how it ended."""
    tail = done.stderr[-TAIL_CHARACTERS:]
    if done.returncode:
        return f"the client ended {done.returncode}: {tail}"
    try:
        return outcome_in(done.stdout)
    except (ValueError, KeyError, TypeError) as error:
        return f"the client's last line is no outcome ({type(error).__name__}): {tail}"


def next_worker_problem(run: Run, before: str) -> tuple[str | None, str]:
    """Await a running worker pod other than `before`; give its problem and its uid."""
    pod, found = await_pod(
        run,
        WORKER_POD,
        lambda pod: (
            pod["metadata"]["uid"] != before
            and "running" in container_status(pod, WORKER_CONTAINER).get("state", {})
        ),
    )
    if not found:
        waited = f"within {TERMINATION_WINDOW_SECONDS} s"
        return f"no worker pod other than {before} was running {waited}", ""
    life = Life(pod["metadata"]["uid"]).after(pod, deleted=False)
    problem = remove_link_problem(life)
    if problem is None and life.restarts:
        problem = f"restartCount is {life.restarts}, expected 0"
    return (f"uid {life.uid}: {problem}" if problem else None), life.uid


def drive_build(run: Run, deployment: Document, web_pod: str) -> str | None:
    """Cell 11: hello-deck built and exported by the web's client in the web pod.

    Give the problem, or none after printing the cell's line.
    """
    first = read_pod(run, WORKER_POD)
    if first is None:
        return f"the worker Pod {WORKER_POD} cannot be read"
    before = first["metadata"]["uid"]
    root = scratch_mount(deployment)
    deck = run.repository / "examples" / HELLO_DECK
    copied = run(
        f"timeout {COPY_SECONDS} kubectl cp",
        *(str(deck), f"{web_pod}:{root}/{HELLO_DECK}"),
        *("--container", WEB_CONTAINER, *IN_NAMESPACE),
        check=False,
    )
    if copied.returncode:
        tail = copied.stderr[-TAIL_CHARACTERS:]
        return f"copying the deck into the web ended {copied.returncode}: {tail}"
    client = run(
        f"timeout {CLIENT_SECONDS} kubectl exec --stdin",
        *(web_pod, "--container", WEB_CONTAINER, *IN_NAMESPACE),
        *("--", WEB_PYTHON, "-I", "-", root, "hello", *HELLO_STEPS),
        stdin=CLIENT_PROGRAM.read_text(encoding="utf-8"),
        check=False,
    )
    outcome = read_outcome(client)
    if isinstance(outcome, str):
        return outcome
    if not delivers_the_deck(outcome):
        return f"hello-deck was not delivered: {outcome}"
    problem, after = next_worker_problem(run, before)
    if problem is None:
        say(
            f"cell 11: hello-deck built and exported through the web (talk "
            f"index.html, PDF %PDF-, out: {', '.join(outcome.out)}); the steps "
            f"began in worker pod {before}, the next pod {after} runs with "
            f"remove-link 0, restarts 0"
        )
    return problem
