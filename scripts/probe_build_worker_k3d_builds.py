"""The programs the k3d probe sends into a pod and what they print.

`copy_deck` puts `examples/hello-deck` into the web pod's scratch directory and
`build_outcome` runs the web's client there, as the container's own user with its
environment: only the web's composition runs, `load_settings`, `the_build_worker`
and the network. The scratch directory is the web container's own `emptyDir`
mount, read from the rendered Deployment; the builds root belongs to the web's
sweep. `run_program` sends any of the probe's programs over stdin and gives the
one JSON line it prints.
"""

import json
from pathlib import Path
from typing import cast

from probe_build_worker_harness import say
from probe_build_worker_k3d_cluster import IN_NAMESPACE, Completed, Document, Run
from probe_build_worker_k3d_manifests import WEB_CONTAINER
from probe_build_worker_web import (
    CLIENT_PROGRAM,
    HELLO_STEPS,
    WEB_PYTHON,
    Outcome,
    outcome_in,
)

HELLO_DECK = "hello-deck"
COPY_SECONDS = 120
CLIENT_SECONDS = 600
PROGRAM_SECONDS = 120
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


def run_program(
    run: Run, pod: str, container: str, program: Path, *arguments: str
) -> Completed:
    """Send the program over stdin to the python of the pod's container."""
    return run(
        f"timeout {PROGRAM_SECONDS} kubectl exec --stdin",
        *(pod, "--container", container, *IN_NAMESPACE),
        *("--", WEB_PYTHON, "-I", "-", *arguments),
        stdin=program.read_text(encoding="utf-8"),
        check=False,
    )


def printed_line(done: Completed) -> dict[str, object] | str:
    """The JSON object a program printed as its last line, or what is wrong."""
    tail = done.stderr[-TAIL_CHARACTERS:]
    if done.returncode:
        return f"the program ended {done.returncode}: {tail}"
    try:
        line: object = json.loads(done.stdout.splitlines()[-1])
    except IndexError, ValueError:
        return f"the program printed no JSON line: {tail}"
    if not isinstance(line, dict):
        return f"the program printed {line!r}"
    return cast("dict[str, object]", line)


def read_outcome(done: Completed) -> Outcome | str:
    """The client's outcome, or what is wrong with how it ended."""
    tail = done.stderr[-TAIL_CHARACTERS:]
    if done.returncode:
        return f"the client ended {done.returncode}: {tail}"
    try:
        return outcome_in(done.stdout)
    except (ValueError, KeyError, TypeError) as error:
        return f"the client's last line is no outcome ({type(error).__name__}): {tail}"


def copy_deck(run: Run, deployment: Document, web_pod: str) -> str | None:
    """Copy hello-deck into the web pod's scratch directory; give the problem."""
    deck = run.repository / "examples" / HELLO_DECK
    copied = run(
        f"timeout {COPY_SECONDS} kubectl cp",
        *(str(deck), f"{web_pod}:{scratch_mount(deployment)}/{HELLO_DECK}"),
        *("--container", WEB_CONTAINER, *IN_NAMESPACE),
        check=False,
    )
    if copied.returncode:
        tail = copied.stderr[-TAIL_CHARACTERS:]
        return f"copying the deck into the web ended {copied.returncode}: {tail}"
    return None


def build_outcome(run: Run, deployment: Document, web_pod: str) -> Outcome | str:
    """Build and export the copied hello-deck by the web's client in the web pod.

    Give the client's outcome, or what is wrong with how it ended.
    """
    client = run(
        f"timeout {CLIENT_SECONDS} kubectl exec --stdin",
        *(web_pod, "--container", WEB_CONTAINER, *IN_NAMESPACE),
        *("--", WEB_PYTHON, "-I", "-", scratch_mount(deployment), *HELLO_STEPS),
        stdin=CLIENT_PROGRAM.read_text(encoding="utf-8"),
        check=False,
    )
    if client.stderr.strip():
        say(f"the web client's log:\n{client.stderr[-TAIL_CHARACTERS:]}")
    return read_outcome(client)
