"""Build hello-deck through the web's own composition.

Usage: `python -I - DECKS [STEP...]`, the program on stdin; DECKS is the
directory that holds `hello-deck`.

Run inside the web's container as its own user. It reads the settings
with `load_settings()`, takes the worker from `the_build_worker` and runs the
named steps of the deck in a fresh run directory under the builds root. It
prints the outcome as its one line: each step's `BuildFailure` reason, text and
counts, and what the run directory holds when the steps are over.
"""

import json
import sys
from pathlib import Path
from shutil import copytree
from tempfile import mkdtemp

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
    for kind in kinds:
        failure = toolchain.ran(
            BuildStep(
                place=place,
                slug=SLUG,
                name=kind,
                arguments=slidev_arguments(kind, place=place, slug=SLUG),
                watched=lambda: None,
            )
        )
        step: dict[str, object] = {"step": kind.value, "failed": failure is not None}
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
