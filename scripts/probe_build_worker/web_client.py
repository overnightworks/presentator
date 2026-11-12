"""Build one deck through the web's own composition.

Usage: `python -I - DECKS CASE [STEP...]`, the program on stdin; DECKS is the
directory that holds the decks of the cases.

Run inside the web's container as its own user. It reads the settings
with `load_settings()`, takes the worker from `the_build_worker` and runs the
named steps of a deck in a fresh run directory under the builds root. It prints
the run's name as the first JSON line, so a driver can watch the run directory,
and the outcome as the last one: each step's `BuildFailure` reason, text and
counts, and what the run directory holds when the steps are over.
"""

import json
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from shutil import copytree
from tempfile import mkdtemp

from presentator.adapters.build_wire import MAXIMUM_DEPTH, MAXIMUM_ENTRIES, MEGABYTE
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

LIMIT_FILE = "limit.txt"
STAGED_PREFIX = "staged-"
EXTRA_PART = "a"
# Every audio worklet module registers its processor, whatever its built file is named.
WORKLET_MARKER = b"registerProcessor("


class Case(StrEnum):
    """The decks this client builds, each by the way it reaches a limit."""

    HELLO = "hello"
    ADDON = "addon"
    HOLD = "hold"
    MANY_ENTRIES = "many-entries"
    OUTPUT_MEGABYTES = "output-megabytes"
    OUTPUT_ENTRIES = "output-entries"
    OUTPUT_SHAPE = "output-shape"
    DECK_MEGABYTES = "deck-megabytes"
    DECK_PATHS = "deck-paths"


@dataclass(frozen=True, slots=True)
class Source:
    """The deck a case starts from, and the file it adds to the run's copy."""

    deck: Path
    limit: str | None = None


def sources_of(decks: Path) -> dict[Case, Source]:
    """Where each case's deck lies under the directory that holds the decks."""
    limits = decks / "probe" / "limits"
    return {
        Case.HELLO: Source(decks / "hello-deck"),
        Case.ADDON: Source(decks / "copresenter-deck"),
        Case.HOLD: Source(decks / "probe" / "flood"),
        Case.MANY_ENTRIES: Source(limits, Case.MANY_ENTRIES),
        Case.OUTPUT_MEGABYTES: Source(limits, Case.OUTPUT_MEGABYTES),
        Case.OUTPUT_ENTRIES: Source(limits, Case.OUTPUT_ENTRIES),
        Case.OUTPUT_SHAPE: Source(limits, Case.OUTPUT_SHAPE),
        Case.DECK_MEGABYTES: Source(limits),
        Case.DECK_PATHS: Source(limits),
    }


def swell(deck: Path, case: Case, megabytes: int) -> None:
    """Make the run's copy of the limits deck too large, or too deeply nested."""
    if case is Case.DECK_MEGABYTES:
        with (deck / "sparse").open("wb") as sparse:
            sparse.truncate(megabytes * MEGABYTE + 1)
    if case is Case.DECK_PATHS:
        deep = deck.joinpath(*[EXTRA_PART] * MAXIMUM_DEPTH)
        deep.mkdir(parents=True)
        (deep / EXTRA_PART).write_text("x", encoding="utf-8")


def prepared_run(case: Case, decks: Path, builds: Path, megabytes: int) -> BuildPlace:
    """A fresh run directory holding the case's deck and an empty out/."""
    run = Path(mkdtemp(dir=builds))
    (run / OUTPUT_DIRECTORY).mkdir()
    source = sources_of(decks)[case]
    deck = run / DECK_DIRECTORY
    copytree(source.deck, deck)
    if source.limit is not None:
        # One more than each bound, so the limits deck reaches exactly the
        # bounds this client holds and has no copy of either number; the
        # many-entries count is nine tenths of the entries bound, large enough
        # to take seconds to send and under the bound.
        case_file = {
            "mode": source.limit,
            "output_megabytes": megabytes + 1,
            "output_entries": MAXIMUM_ENTRIES + 1,
            "many_entries": MAXIMUM_ENTRIES * 9 // 10,
        }
        (deck / LIMIT_FILE).write_text(json.dumps(case_file), encoding="utf-8")
    swell(deck, case, megabytes)
    return BuildPlace(root=builds, run=Path(run.name))


def staged_names(place: BuildPlace) -> list[str]:
    """The staging directories a step left in the run directory."""
    return sorted(
        path.name
        for path in place.here.iterdir()
        if path.name.startswith(STAGED_PREFIX)
    )


def delivered(place: BuildPlace) -> dict[str, bool]:
    """What reached out/: the talk's page, the PDF and the capture worklet."""
    out = place.here / OUTPUT_DIRECTORY
    pdf = out / PDF_FILE
    worklets = [
        asset
        for asset in (out / TALK_DIRECTORY / "assets").glob("*.js")
        if WORKLET_MARKER in asset.read_bytes()
    ]
    return {
        "talk_page": (out / TALK_DIRECTORY / "index.html").is_file(),
        "pdf": pdf.is_file() and pdf.read_bytes().startswith(b"%PDF-"),
        "worklet": len(worklets) == 1,
    }


def main(decks: Path, case: Case, kinds: list[StepKind]) -> None:
    """Run the steps and print the run's name, then the outcome."""
    settings = load_settings()
    toolchain = the_build_worker(settings, bound=build_bound_of(settings))
    place = prepared_run(case, decks, settings.builds, settings.build_output_megabytes)
    sys.stdout.write(json.dumps({"run": place.run.name}) + "\n")
    sys.stdout.flush()
    steps: list[dict[str, object]] = []
    for kind in kinds:
        failure = toolchain.ran(
            BuildStep(
                place=place,
                slug=case.value,
                name=kind,
                arguments=slidev_arguments(kind, place=place, slug=case.value),
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
                "staged": staged_names(place),
                "out": sorted(
                    path.name for path in (place.here / OUTPUT_DIRECTORY).iterdir()
                ),
            }
            | delivered(place)
        )
        + "\n"
    )


if __name__ == "__main__":
    main(
        Path(sys.argv[1]),
        Case(sys.argv[2]),
        [StepKind(name) for name in sys.argv[3:]] or list(StepKind),
    )
