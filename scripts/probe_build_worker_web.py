"""What the k3d probe sends into its pods and what it reads of the web's client.

The client program `probe_build_worker/web_client.py` is sent into the web pod
over stdin; it builds hello-deck through the very composition the web serves
with and prints its outcome as its last line, which `outcome_in` reads. The
hello and egress programs are sent the same way and print one JSON line.
"""

import json
from dataclasses import dataclass

from probe_build_worker_harness import PROBE_SOURCES

WEB_PYTHON = "/opt/venv/bin/python"
CLIENT_PROGRAM = PROBE_SOURCES / "web_client.py"
HELLO_PROGRAM = PROBE_SOURCES / "hello_client.py"
EGRESS_PROGRAM = PROBE_SOURCES / "egress_probe.py"
HELLO_STEPS = ("build", "export")


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
    out: tuple[str, ...]
    talk_page: bool
    pdf: bool


def outcome_in(stdout: str) -> Outcome:
    """The outcome in the client's last printed line."""
    lines = stdout.splitlines()
    if not lines:
        message = "the web client printed nothing"
        raise ValueError(message)
    said = json.loads(lines[-1])
    return Outcome(
        steps=tuple(
            StepOutcome(step["step"], step["failed"], step.get("failure"))
            for step in said["steps"]
        ),
        out=tuple(said["out"]),
        talk_page=said["talk_page"],
        pdf=said["pdf"],
    )


def delivers_the_deck(done: Outcome) -> bool:
    """Whether the build and the export ran unfailed and left the talk page and PDF."""
    return (
        tuple(step.step for step in done.steps) == HELLO_STEPS
        and not any(step.failed for step in done.steps)
        and done.talk_page
        and done.pdf
    )


def is_an_outage(done: Outcome) -> bool:
    """Whether the first step found no worker: unavailable, with no failure text."""
    return (
        len(done.steps) == 1
        and done.steps[0].failed
        and done.steps[0].failure is None
        and not done.out
    )
