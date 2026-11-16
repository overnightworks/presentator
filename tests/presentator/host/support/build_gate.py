"""A toolchain stand-in whose builds wait until the test lets them through."""

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.presentator.adapters.support.build_support import FINDS_ITS_OUTPUT
from tests.support.git_remote import with_the_program

_TOOLCHAIN_PROGRAM = "pnpm"
# A talk's build reads one line from the gate before it writes; its PDF export,
# a separate call, does not wait.
_GATED_TOOLCHAIN = (
    FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *) read -r line < "{gate}"; mkdir -p "$out"; printf 'a talk' > "$out/index.html";;
esac
"""
)


class BuildGate:
    """The pipe the stand-in reads: a build passes once for each line written."""

    def __init__(self, pipe: Path) -> None:
        """Make the pipe and open it for both reading and writing."""
        os.mkfifo(pipe)
        # Both ends in one descriptor, so a build that opens the pipe neither
        # waits for a writer nor ever reads the end of the file.
        self._descriptor = os.open(pipe, os.O_RDWR)

    def let_through(self, builds: int) -> None:
        """Let this many builds, those waiting and those still to start, run."""
        os.write(self._descriptor, b"go\n" * builds)

    def close(self) -> None:
        """Close the pipe's one descriptor."""
        os.close(self._descriptor)


@contextmanager
def gated_toolchain(
    machine: pytest.MonkeyPatch, tmp_path: Path
) -> Generator[BuildGate]:
    """A `pnpm` on PATH whose builds wait at the gate, closed when the block ends."""
    pipe = tmp_path / "build-gate"
    gate = BuildGate(pipe)
    try:
        with_the_program(
            machine, tmp_path, _TOOLCHAIN_PROGRAM, _GATED_TOOLCHAIN.format(gate=pipe)
        )
        yield gate
    finally:
        gate.close()
