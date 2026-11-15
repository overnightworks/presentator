"""Build hello-deck through the web's own composition.

Usage: `python -I - DECKS [STEP...]`, the program on stdin; DECKS is the
directory that holds `hello-deck`.

Run inside the web's container as its own user. It reads the settings
with `load_settings()`, takes the worker from `the_build_worker` and runs the
named steps of the deck in a fresh run directory under the builds root. It
prints the outcome as its one line: each step's admission time (when a worker
had answered its header) and `BuildFailure` reason, text and counts, and what
the run directory holds when the steps are over.
"""

import json
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from shutil import copytree
from tempfile import mkdtemp

from presentator.adapters import build_worker
from presentator.adapters.builds import BuildPlace, BuildStep, slidev_arguments
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    TALK_DIRECTORY,
    BuildFailure,
    StepKind,
)
from presentator.host.config import load_settings
from presentator.host.main import build_bound_of, the_build_worker

HELLO_DECK = "hello-deck"
SLUG = "hello"


def noting_admissions(admitted: list[float]) -> None:
    """Note the time of each step's exchange with a worker, which follows its STATUS."""
    exchange = build_worker.exchange_with_worker

    def exchange_noting_the_time(
        connection: socket.socket,
        *,
        step: BuildStep,
        deck: int,
        megabytes: int,
        remove_staging: Callable[[Path], None],
    ) -> BuildFailure | None:
        admitted.append(time.time())
        return exchange(
            connection,
            step=step,
            deck=deck,
            megabytes=megabytes,
            remove_staging=remove_staging,
        )

    build_worker.exchange_with_worker = exchange_noting_the_time


def prepared_run(decks: Path, builds: Path) -> BuildPlace:
    """A fresh run directory holding the hello deck and an empty out/."""
    run = Path(mkdtemp(dir=builds))
    (run / OUTPUT_DIRECTORY).mkdir()
    copytree(decks / HELLO_DECK, run / DECK_DIRECTORY)
    return BuildPlace(root=builds, run=Path(run.name))


def delivered(place: BuildPlace) -> dict[str, bool]:
    """What reached out/: the talk's page and the PDF."""
    out = place.here / OUTPUT_DIRECTORY
    pdf = out / PDF_FILE
    return {
        "talk_page": (out / TALK_DIRECTORY / "index.html").is_file(),
        "pdf": pdf.is_file() and pdf.read_bytes().startswith(b"%PDF-"),
    }


def main(decks: Path, kinds: list[StepKind]) -> None:
    """Run the steps and print the outcome."""
    settings = load_settings()
    toolchain = the_build_worker(settings, bound=build_bound_of(settings))
    place = prepared_run(decks, settings.builds)
    steps: list[dict[str, object]] = []
    admitted: list[float] = []
    noting_admissions(admitted)
    for kind in kinds:
        admitted.clear()
        failure = toolchain.ran(
            BuildStep(
                place=place,
                slug=SLUG,
                name=kind,
                arguments=slidev_arguments(kind, place=place, slug=SLUG),
                watched=lambda: None,
            )
        )
        step: dict[str, object] = {
            "step": kind.value,
            "failed": failure is not None,
            "admitted": admitted[0] if admitted else None,
        }
        if isinstance(failure, BuildFailure):
            step["failure"] = {
                "reason": failure.reason.value,
                "text": failure.text,
                "written_megabytes": failure.written_megabytes,
                "allowed_megabytes": failure.allowed_megabytes,
                "allowed_entries": failure.allowed_entries,
            }
        steps.append(step)
        if failure is not None:
            break
    sys.stdout.write(
        json.dumps(
            {
                "steps": steps,
                "out": sorted(
                    path.name for path in (place.here / OUTPUT_DIRECTORY).iterdir()
                ),
            }
            | delivered(place)
        )
        + "\n"
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]), [StepKind(name) for name in sys.argv[2:]] or list(StepKind))
