"""Drive the deployed web against the sealed worker, from the instance image.

Usage: probe_build_worker_web.py drive

Runs where Docker is, after the worker's own drive has removed its project, with
the same compose environment plus `WEB_PROBE_OVERRIDE`, the override file that
adds the web's three read-only probe mounts (hello-deck, copresenter-deck and
`probe_build_worker`), publishes the probe's port, which `compose.yaml` never
does, and carries the job's origins and test issuer; the master key and the
client secret are compose's own file secrets. It starts the real `presentator`
service, which has no Docker socket and no neighbour but the worker, and sends
the program `probe_build_worker/web_client.py` into it over stdin: that program builds
through the very composition the web serves with. Every cell prints one line,
and a cell that is not driven fails the run.

Precondition: the override sets `PRESENTATOR_SOURCE_POLL_SECONDS` above this
run's duration, as the CI job does. The web sweeps every build run no deck row
names at the end of a poll, and the program lays runs of its own under the
builds root that no row names.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from urllib.parse import urlsplit

from probe_build_worker_harness import (
    PROBE_SOURCES,
    REPOSITORY,
    SERVICE,
    Report,
    compose,
    docker,
    refuse,
    say,
    state,
    worker,
)
from probe_build_worker_lives import (
    MODE_FILE,
    FloodMode,
    docker_time,
    handshake_line,
    wait_for_uptime,
)
from ruamel.yaml import YAML

from presentator.adapters.build_wire import MAXIMUM_ENTRIES, SOCKET_DIRECTORY
from presentator.adapters.build_worker import CONNECT_WINDOW_SECONDS
from presentator.contracts.decks import BuildFailureReason
from presentator.host.config import DEFAULT_BUILD_OUTPUT_MEGABYTES
from presentator.host.main import WAITING_FOR_THE_WORKER

WEB = "presentator"
CLIENT_PROGRAM = PROBE_SOURCES / "web_client.py"
ROUTE = "http://127.0.0.1:8000/login"
BUILDS_ROOT = "/data/builds"
OVERRIDE_MOUNTS = frozenset(
    {"/decks/hello-deck", "/decks/copresenter-deck", "/decks/probe"}
)
SECRET_MOUNTS = frozenset(
    {"/run/secrets/presentator_secret_key", "/run/secrets/oidc_client_secret"}
)
COMPOSE_MOUNTS = frozenset(
    {"/data/database", "/data/mirrors", BUILDS_ROOT}
    | {SOCKET_DIRECTORY}
    | SECRET_MOUNTS
)
TEMPORARY = "/tmp"  # noqa: S108 - the path is inside the worker container and not on this host
ROUTE_POLL_SECONDS = 0.5
WAITING_HOLD_SECONDS = 4.0
STEP_SECONDS = 900
STAGING_POLLS = 1200
RESTART_SECONDS = 60
CELLS = ("B25", "whole file", "B12 C", "B13", "B15 C", "addon deck")
HELLO_STEPS = ("build", "export")
ADDON_WORKLET = REPOSITORY / "slidev/worklets/pcm-capture-processor.js"


def web_command(*arguments: str) -> tuple[str, ...]:
    """The docker compose command line for the project with the probe override."""
    return (
        "docker", "compose",
        "--file", str(REPOSITORY / "compose.yaml"),
        "--file", os.environ["WEB_PROBE_OVERRIDE"],
        *arguments,
    )  # fmt: skip


def web(*arguments: str) -> str:
    """Run docker compose with the override, failing loudly."""
    return subprocess.run(  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
        web_command(*arguments), check=True, capture_output=True, text=True
    ).stdout.strip()


def in_web(*command: str) -> subprocess.CompletedProcess[str]:
    """Run a command in the web; the caller judges the status."""
    return subprocess.run(  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
        web_command("exec", "-T", WEB, *command),
        check=False,
        capture_output=True,
        text=True,
    )


def start_client(case: str, *steps: str) -> subprocess.Popen[str]:
    """Send the client program into the web; it prints its run's name first."""
    client = subprocess.Popen(  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
        web_command("exec", "-T", WEB, "/opt/venv/bin/python", "-I", "-", case, *steps),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )  # fmt: skip
    if client.stdin is None:
        refuse("the web client has no stdin")
    client.stdin.write(CLIENT_PROGRAM.read_text(encoding="utf-8"))
    client.stdin.close()
    client.stdin = None  # communicate() would flush the closed stream and raise
    return client


def run_name_of(client: subprocess.Popen[str]) -> str:
    """The run directory the client builds in, from its first line."""
    if client.stdout is None:
        refuse("the web client has no stdout")
    first = handshake_line(client, client.stdout)
    return str(json.loads(first)["run"])


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """How one step of a client run ended: the failure the build reported, if any."""

    step: str
    failed: bool
    failure: dict[str, str | int | None] | None


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a client run reports: its steps and what its run directory holds."""

    steps: tuple[StepOutcome, ...]
    staged: tuple[str, ...]
    out: tuple[str, ...]
    talk_page: bool
    pdf: bool
    worklet: bool
    worklet_sha256: str | None


def outcome_of(client: subprocess.Popen[str]) -> Outcome:
    """The client's last line, after it has ended."""
    try:
        stdout, stderr = client.communicate(timeout=STEP_SECONDS)
    except subprocess.TimeoutExpired:
        client.kill()
        refuse(f"the web client did not end within {STEP_SECONDS} seconds")
    lines = stdout.splitlines()
    if client.returncode or not lines:
        refuse(f"the web client failed: {client.returncode} {stderr}")
    said = json.loads(lines[-1])
    return Outcome(
        steps=tuple(
            StepOutcome(step["step"], step["failed"], step.get("failure"))
            for step in said["steps"]
        ),
        staged=tuple(said["staged"]),
        out=tuple(said["out"]),
        talk_page=said["talk_page"],
        pdf=said["pdf"],
        worklet=said["worklet"],
        worklet_sha256=said["worklet_sha256"],
    )


def build(case: str, *steps: str) -> Outcome:
    """Run the client to its end and return its outcome."""
    client = start_client(case, *steps)
    run_name_of(client)
    return outcome_of(client)


def web_answers() -> bool:
    """Whether the signed-out page answers 200 over the published port."""
    host = urlsplit(os.environ["PRESENTATOR_PUBLIC_ORIGIN"]).hostname or ""
    request = urllib.request.Request(ROUTE, headers={"Host": host})
    try:
        with urllib.request.urlopen(request, timeout=5) as answer:  # noqa: S310 - the URL is the probe stack's own published route over http
            return bool(answer.status == HTTPStatus.OK)
    except OSError:
        return False


def wait_until_the_web_answers() -> float:
    """The seconds until the web answers, within the connect window."""
    started = time.monotonic()
    while not web_answers():
        if time.monotonic() - started > CONNECT_WINDOW_SECONDS:
            refuse(f"the web did not answer within {CONNECT_WINDOW_SECONDS:g} seconds")
        time.sleep(ROUTE_POLL_SECONDS)
    return time.monotonic() - started


def restart_count_is(expected: int) -> None:
    """Fail unless the worker restarted exactly to this count, judged by the count."""
    deadline = time.monotonic() + RESTART_SECONDS
    while int(state()[1]) != expected:
        if time.monotonic() > deadline:
            refuse(f"the worker's restart count is {state()[1]}, expected {expected}")
        time.sleep(ROUTE_POLL_SECONDS)


def the_next_build_delivers_hello_deck() -> None:
    """A fresh run of hello-deck builds and exports through the restarted worker."""
    done = build("hello", *HELLO_STEPS)
    if (
        tuple(step.step for step in done.steps) != HELLO_STEPS
        or any(step.failed for step in done.steps)
        or not (done.talk_page and done.pdf)
    ):
        refuse(f"hello-deck was not delivered: {done}")


def refuse_if_the_web_answers(why: str) -> None:
    """Fail when the web serves although it must still be waiting."""
    if web_answers():
        refuse(f"the web answered {why}")


def refuse_unless_after_the_worker_began(answered: datetime) -> None:
    """Fail unless the first answer came after the worker's current life began."""
    started, _ = state()
    if answered < docker_time(started):
        refuse("the web answered before the worker's current life began")


def refuse_unless_the_web_logged_its_wait() -> None:
    """Fail unless the web says in its log that it waits for the worker."""
    said = WAITING_FOR_THE_WORKER.split("%", 1)[0]
    started = time.monotonic()
    while said not in web("logs", "--no-color", WEB):
        if time.monotonic() - started > CONNECT_WINDOW_SECONDS:
            refuse("the web never logged that it waits for the worker")
        time.sleep(ROUTE_POLL_SECONDS)


def refuse_unless_the_web_keeps_waiting() -> None:
    """Fail unless the web, once it logged its wait, stays unserved and alive."""
    refuse_unless_the_web_logged_its_wait()
    deadline = time.monotonic() + WAITING_HOLD_SECONDS
    while time.monotonic() < deadline:
        refuse_if_the_web_answers("while waiting for the worker, before it was started")
        if not web("ps", "--quiet", WEB).strip():
            refuse("the web exited instead of waiting for the worker")
        time.sleep(ROUTE_POLL_SECONDS)


def prove_inspected_shape() -> None:
    """The web holds no socket, no group, the hardening keys and only ruled mounts."""
    shape = json.loads(docker("inspect", web("ps", "--quiet", WEB)))[0]
    mounts = {
        mount["Destination"]: mount
        for mount in shape["Mounts"]
        if not (mount["Type"] == "tmpfs" and mount["Destination"] == TEMPORARY)
    }
    host = shape["HostConfig"]
    if "docker.sock" in json.dumps(shape["Mounts"]) or host["GroupAdd"]:
        refuse(f"the web holds a socket or a group: {host['GroupAdd']}")
    if (
        host["CapDrop"] != ["ALL"]
        or host["SecurityOpt"] != ["no-new-privileges:true"]
        or host["ReadonlyRootfs"] is not True
    ):
        refuse(f"the web is not hardened: {host}")
    if frozenset(mounts) != COMPOSE_MOUNTS | OVERRIDE_MOUNTS or any(
        mounts[destination]["RW"] for destination in OVERRIDE_MOUNTS | SECRET_MOUNTS
    ):
        refuse(f"the web's mounts are {sorted(mounts)}")


def prove_the_whole_file_starts(report: Report) -> None:
    """The file starts web healthy and worker running; its proxy is the gateway.

    The stack B25 left running is started again, so this proves the web's health
    and the worker's state, not a fresh create.
    """
    web("up", "--detach", "--wait", "--no-build")
    health = docker(
        "inspect", "--format", "{{.State.Health.Status}}", web("ps", "--quiet", WEB)
    )
    running = docker("inspect", "--format", "{{.State.Status}}", worker())
    if (health, running) != ("healthy", "running"):
        refuse(
            f"after the whole file started the web is {health}, the worker {running}"
        )
    declared = YAML(typ="safe").load(  # pyright: ignore[reportUnknownMemberType]
        (REPOSITORY / "compose.yaml").read_text(encoding="utf-8")
    )
    trusted = declared["services"][WEB]["environment"]["PRESENTATOR_TRUSTED_PROXIES"]
    network = json.loads(
        docker("network", "inspect", declared["networks"]["default"]["name"])
    )
    (pool,) = network[0]["IPAM"]["Config"]
    gateway = pool["Gateway"]
    if gateway != trusted:
        refuse(f"the gateway {gateway} is not the trusted proxy")
    report.cell(
        "whole file", f"web healthy, worker running, gateway {gateway} is trusted"
    )


def prove_start_before_the_worker(report: Report) -> None:
    """B25: a web on a fresh socket volume waits, and answers within W of the worker."""
    volume = f"{os.environ['COMPOSE_PROJECT_NAME']}_build-socket"
    if docker("ps", "--all", "--quiet", "--filter", f"volume={volume}"):
        refuse("a container still holds the socket volume")
    web("up", "--detach", "--no-build", "--no-deps", WEB)
    fresh = in_web("stat", "-c", "%a %u %g", SOCKET_DIRECTORY).stdout.strip()
    if fresh != "750 0 0":
        refuse(f"the socket volume is not fresh: {fresh}")
    refuse_unless_the_web_keeps_waiting()
    web("up", "--detach", "--no-build", "--no-deps", SERVICE)
    seconds = wait_until_the_web_answers()
    refuse_unless_after_the_worker_began(datetime.now(UTC))
    if "did not answer" in web("logs", "--no-color", WEB):
        refuse("the web's log holds a start refusal")
    prove_inspected_shape()
    scratch = in_web(
        "/opt/venv/bin/python", "-I", "-c", "import tempfile; print(tempfile.mkdtemp())"
    )
    ssh = in_web(
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=2",
        "-o", "UserKnownHostsFile=/data/mirrors/known_hosts",
        "-p", "9", "probe@127.0.0.1", "true",
    )  # fmt: skip
    if (
        not scratch.stdout.startswith(TEMPORARY)
        or "Connection refused" not in ssh.stderr
    ):
        refuse(f"the read-only root: {scratch} {ssh}")
    report.cell(
        "B25",
        f"fresh volume 750 0 0; the web answered {seconds:.1f} s after the worker;"
        " no socket, no group, hardened, ruled mounts only; tempfile and ssh start",
    )


def prove_restart_mid_step(report: Report) -> None:
    """B12 C: the web restarts mid-step, starts after the worker's life, builds."""
    wait_for_uptime()
    docker("exec", worker(), "sh", "-c", f"echo {FloodMode.HOLD.value} > {MODE_FILE}")
    _, restarts = state()
    held = start_client("hold", "build")
    run_name_of(held)
    while subprocess.run(  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
        ("docker", "exec", worker(), "test", "-s", f"{TEMPORARY}/deck.pid"),  # noqa: S607 - docker is picked from the operator's trusted PATH on the operator's own machine as when run by hand
        check=False,
    ).returncode:
        if held.poll() is not None:
            refuse("the held step ended before the deck ran")
        time.sleep(ROUTE_POLL_SECONDS)
    web("restart", "--no-deps", WEB)
    held.wait()
    seconds = wait_until_the_web_answers()
    answered = datetime.now(UTC)
    restart_count_is(int(restarts) + 1)
    refuse_unless_after_the_worker_began(answered)
    the_next_build_delivers_hello_deck()
    report.cell(
        "B12 C",
        f"restart count {restarts} -> {int(restarts) + 1}; the web answered"
        f" {seconds:.1f} s after its start; hello-deck delivered with %PDF-",
    )


def prove_reply_cut_off(report: Report) -> None:
    """B13: the worker killed mid-reply is a failure, and the next build succeeds."""
    wait_for_uptime()
    _, restarts = state()
    client = start_client("many-entries", "build")
    run = run_name_of(client)
    staged = f"{BUILDS_ROOT}/{run}/staged-build"
    waited = in_web(
        "sh", "-c",
        f"for _ in $(seq {STAGING_POLLS}); do"
        f' [ -n "$(ls -A {staged} 2>/dev/null)" ] && exit 0; sleep 0.05; done; exit 1',
    )  # fmt: skip
    if waited.returncode or client.poll() is not None:
        refuse(f"the reply ended before the worker was killed: {waited}")
    pid = docker("inspect", "--format", "{{.State.Pid}}", worker())
    subprocess.run(("sudo", "-n", "kill", "-KILL", pid), check=True)  # noqa: S603, S607 - argv is a tuple of literal words and probe values and run without a shell; sudo is picked from the operator's trusted PATH on the operator's own machine as when run by hand
    done = outcome_of(client)
    if (
        [reason_of(step) for step in done.steps] != [BuildFailureReason.NO_WORDS.value]
        or done.staged
        or done.out
    ):
        refuse(f"the cut-off reply: {done}")
    restart_count_is(int(restarts) + 1)
    the_next_build_delivers_hello_deck()
    report.cell(
        "B13",
        f"BuildFailure(no-words); staging gone, out/ empty; restart count"
        f" {restarts} -> {int(restarts) + 1}; the next build delivered",
    )


def reason_of(step: StepOutcome) -> object:
    """The reason code a step failed with, or nothing while it did not fail."""
    return None if step.failure is None else step.failure["reason"]


def failure_of(case: str) -> dict[str, str | int | None]:
    """The failure of the one failed build step of a case, whatever it ended in."""
    steps = build(case, "build").steps
    if len(steps) != 1 or steps[0].failure is None:
        refuse(f"{case} did not fail: {steps}")
    return steps[0].failure


def failure_naming(
    reason: BuildFailureReason, **counts: str | int | None
) -> dict[str, str | int | None]:
    """The failure a reason is reported as: no text, and the counts it names."""
    return {
        "reason": reason.value,
        "text": None,
        "written_megabytes": None,
        "allowed_megabytes": None,
        "allowed_entries": None,
    } | counts


def prove_refusal_reasons(report: Report) -> None:
    """B15 C: each limit is reported as its reason code with the counts it names."""
    allowed = DEFAULT_BUILD_OUTPUT_MEGABYTES
    expected = {
        "output-entries": failure_naming(
            BuildFailureReason.OUTPUT_ENTRIES, allowed_entries=MAXIMUM_ENTRIES
        ),
        "output-shape": failure_naming(BuildFailureReason.OUTPUT_SHAPE),
    }
    for case, failure in expected.items():
        wait_for_uptime()
        if failure_of(case) != failure:
            refuse(f"{case}: the failure is not {failure!r}")
    wait_for_uptime()
    megabytes = failure_of("output-megabytes")
    written = megabytes["written_megabytes"]
    if (
        megabytes
        != failure_naming(
            BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=written,
            allowed_megabytes=allowed,
        )
        or not isinstance(written, int)
        or written <= allowed
    ):
        refuse("output-megabytes: the failure does not name a size over the bound")
    restarts, log = state()[1], compose("logs", "--no-color", SERVICE)
    deck_failures = {
        "deck-megabytes": failure_naming(
            BuildFailureReason.DECK_MEGABYTES,
            allowed_megabytes=allowed,
            allowed_entries=MAXIMUM_ENTRIES,
        ),
        "deck-paths": failure_naming(BuildFailureReason.DECK_PATHS),
    }
    for case, failure in deck_failures.items():
        if failure_of(case) != failure:
            refuse(f"{case}: the failure is not {failure!r}")
    if (state()[1], compose("logs", "--no-color", SERVICE)) != (restarts, log):
        refuse("a deck refused before the connection touched the worker")
    report.cell(
        "B15 C",
        "five refusals reported as their reasons; deck cases left the worker alone",
    )


def prove_addon_deck(report: Report) -> None:
    """The example deck, which enables the addon by name, builds with its worklet."""
    done = build("addon", *HELLO_STEPS)
    expected = hashlib.sha256(ADDON_WORKLET.read_bytes()).hexdigest()
    if (
        tuple(step.step for step in done.steps) != HELLO_STEPS
        or any(step.failed for step in done.steps)
        or not (done.talk_page and done.pdf)
        or not done.worklet
        or done.worklet_sha256 != expected
    ):
        refuse(f"the addon deck was not delivered with its worklet: {done}")
    report.cell(
        "addon deck", "built and exported; the talk carries the addon's worklet"
    )


def drive() -> None:
    """Start the web beside the worker and drive every cell."""
    report = Report(CELLS)
    try:
        prove_start_before_the_worker(report)
        prove_the_whole_file_starts(report)
        prove_restart_mid_step(report)
        prove_reply_cut_off(report)
        prove_refusal_reasons(report)
        prove_addon_deck(report)
    finally:
        for service in (WEB, SERVICE):
            say(f"--- {service} log")
            subprocess.run(web_command("logs", "--no-color", service), check=False)  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
    report.finish()


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["drive"]:
            drive()
        case _:
            sys.exit(__doc__)
