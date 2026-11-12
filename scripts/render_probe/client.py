"""Build and export the render probe's deck in the image, as the worker runs a step.

Usage: `python -I /probe/client.py DESTINATION`, run by the image's CI step with
the probe directory mounted at `/probe`, `/work` a tmpfs and the network off. The
step launcher the worker starts, `presentator.host.worker_step`, runs once for
the build and once for the export, each with its step record on a file
descriptor, the run's work directory and the toolchain's directory as its
working directory, as the worker starts it. The talk and the PDF are copied to
DESTINATION. The last line printed says where they went and each step's exit
status.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryFile

from presentator.adapters.build_wire import StepHeader, write_record
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    TALK_DIRECTORY,
    StepKind,
)
from presentator.host.worker_scratch import prepare_step_paths
from presentator.host.worker_wire import STEP_ERROR_FILE

PROBE = Path("/probe")
WORK = Path("/work")
SLUG = "render-probe"
STEPS = (StepKind.BUILD, StepKind.EXPORT)


def run_step(kind: StepKind, error: int) -> int:
    """Start the step launcher for one step, and return its exit status."""
    with TemporaryFile() as record:
        write_record(record.fileno(), StepHeader(step=kind, slug=SLUG))
        completed = subprocess.run(  # noqa: S603 - no shell
            (
                sys.executable,
                "-I",
                "-m",
                "presentator.host.worker_step",
                "--record-fd",
                str(record.fileno()),
                "--work",
                str(WORK),
            ),
            cwd=os.environ["PRESENTATOR_TOOLCHAIN"],
            pass_fds=(record.fileno(),),
            stdout=subprocess.DEVNULL,
            stderr=error,
            check=False,
        )
    return completed.returncode


def run_steps() -> dict[str, int]:
    """Run the build, then the export unless the build failed; name each exit status."""
    shutil.copytree(PROBE / DECK_DIRECTORY, WORK / DECK_DIRECTORY)
    error = prepare_step_paths(WORK)
    statuses: dict[str, int] = {}
    try:
        for kind in STEPS:
            statuses[kind.value] = run_step(kind, error)
            if statuses[kind.value] != 0:
                break
    finally:
        os.close(error)
    return statuses


def main(destination: Path) -> int:
    """Run the steps, copy the outputs out when both passed, and say what happened."""
    statuses = run_steps()
    passed = all(status == 0 for status in statuses.values())
    report: dict[str, object] = {"exit_statuses": statuses}
    if passed:
        destination.mkdir(parents=True)
        output = WORK / OUTPUT_DIRECTORY
        shutil.copytree(output / TALK_DIRECTORY, destination / TALK_DIRECTORY)
        shutil.copy2(output / PDF_FILE, destination / PDF_FILE)
        report["talk"] = str(destination / TALK_DIRECTORY)
        report["pdf"] = str(destination / PDF_FILE)
    else:
        sys.stderr.write((WORK / STEP_ERROR_FILE).read_text())
    sys.stdout.write(json.dumps(report) + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
