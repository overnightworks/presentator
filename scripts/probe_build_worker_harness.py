"""What the k3d probe's modules share: the paths and the two printing helpers.

It imports nothing but the standard library, so every probe module resolves it.
"""

import sys
from pathlib import Path
from typing import NoReturn

REPOSITORY = Path(__file__).parents[1]
PROBE_SOURCES = Path(__file__).resolve().parent / "probe_build_worker"


def refuse(claim: str) -> NoReturn:
    """Stop with the claim that did not hold."""
    sys.stderr.write(f"probe failed: {claim}\n")
    sys.exit(1)


def say(line: str) -> None:
    """Print one line and flush it, so a hung probe shows where it stopped."""
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()
