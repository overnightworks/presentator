"""Cells 61 to 65: every start proves the worker alone and its scratch places empty.

Each start is a one-off container of the worker service, as cell 52's sealed
check is. `probe_build_worker/plant_residue.py` runs in it first, on stdin, plants one
residue and replaces itself with the service's own command, so the worker is
PID 1 of a container that holds the residue. The refusals run the full start,
and the control passes `--check`. The service itself stays up beside them.
"""

import json
import subprocess

from probe_build_worker_harness import (
    PROBE_SOURCES,
    REPOSITORY,
    SERVICE,
    Report,
    compose,
    probe_client,
    refuse,
    say,
    state,
)

from presentator.host.worker import REFUSAL_EXIT_STATUS
from presentator.host.worker_self_check import BUILD_IDENTITY, Predicate

RESIDUE_SOURCE = PROBE_SOURCES / "plant_residue.py"
START_SECONDS = 120
PLANT_FAILED_STATUS = 1
CHECK_ARGUMENT = "--check"
REFUSALS = (
    ("61", Predicate.ALONE, ("process",)),
    ("62", Predicate.SCRATCH, ("work-entry", "closed-directory", "shared-memory-file")),
    ("63", Predicate.SCRATCH, ("message-queue",)),
    ("64", Predicate.SCRATCH, ("sysv-shm", "sysv-msg", "sysv-sem")),
)


def service_command() -> list[str]:
    """The command the worker service starts with."""
    configuration = json.loads(compose("config", "--format", "json"))
    return configuration["services"][SERVICE]["command"]


def staged_start(residue: str, command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the command as PID 1 of a one-off container holding the residue."""
    one_off = f"{SERVICE}-staged-{residue}"
    identity = ":".join(map(str, BUILD_IDENTITY))
    try:
        done = subprocess.run(  # noqa: S603 - argv is a tuple of literal words and probe values and run without a shell
            (  # noqa: S607 - docker is picked from the operator's trusted PATH on the operator's own machine as when run by hand
                "docker",
                "compose",
                "--file",
                str(REPOSITORY / "compose.yaml"),
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--name",
                one_off,
                "--entrypoint",
                "python",
                SERVICE,
                "-I",
                "-",
                residue,
                identity,
                *command,
            ),
            input=RESIDUE_SOURCE.read_text(),
            check=False,
            capture_output=True,
            text=True,
            timeout=START_SECONDS,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(("docker", "rm", "--force", one_off), check=False)  # noqa: S603, S607 - argv is a tuple of literal words and probe values and run without a shell; docker is picked from the operator's trusted PATH on the operator's own machine as when run by hand
        refuse(f"staged start {residue}: no exit within {START_SECONDS} s")
    if done.returncode == PLANT_FAILED_STATUS:
        refuse(f"staged start {residue}: the plant failed: {done.stderr}")
    return done


def prove_refusal(residue: str, predicate: Predicate, command: list[str]) -> None:
    """The full start exits 2 naming the predicate."""
    done = staged_start(residue, command)
    if (
        done.returncode != REFUSAL_EXIT_STATUS
        or f"start refused: {predicate}" not in done.stderr
    ):
        refuse(f"start over {residue}: exit {done.returncode}: {done.stderr}")


def prove_refused_starts(report: Report) -> None:
    """Cells 65, then 61 to 64, and the service left alone by all of them."""
    command = service_command()
    before = state()
    done = staged_start("nothing", [*command, CHECK_ARGUMENT])
    if done.returncode:
        refuse(f"--check over nothing: exit {done.returncode}: {done.stderr}")
    report.cell("65", "a clean staged start passes --check, exit 0")
    for cell, predicate, residues in REFUSALS:
        for residue in residues:
            prove_refusal(residue, predicate, command)
        report.cell(
            cell,
            f"{', '.join(residues)}: exit {REFUSAL_EXIT_STATUS}, "
            f"start refused: {predicate}",
        )
    say(f"STATUS after the staged starts {probe_client('hello')}")
    if state() != before:
        refuse("the staged starts changed the service's start time or restarts")
