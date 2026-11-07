"""The build worker's scratch places: where a step writes and a start looks."""

import os
from pathlib import Path

from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    TOOLCHAIN_SCRATCH_DIRECTORY,
)
from presentator.host.worker_wire import STEP_ERROR_FILE

ROOT_MODE = 0o755
WORK_MOUNT = "/work"
TEMPORARY_MOUNT = "/tmp"  # noqa: S108 - a mount point inside the build life
SHARED_MEMORY_MOUNT = "/dev/shm"  # noqa: S108 - a mount point inside the build life
HOME_DIRECTORY = "home"
VITE_DIRECTORY = "vite"
OPEN_DIRECTORY_MODE = 0o1777
STEP_ERROR_MODE = 0o644
STEP_ERROR_FLAGS = (
    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_APPEND | os.O_CLOEXEC
)


def make_open_directory(path: Path) -> None:
    """Make a world-writable directory for a child; never reuse one."""
    path.mkdir()
    path.chmod(OPEN_DIRECTORY_MODE)


def prepare_step_paths(work: Path) -> int:
    """Make the step's writable paths; return the descriptor of its new step.err.

    An existing entry raises. The mode is set explicitly because the umask
    would narrow it.
    """
    for directory in (
        work / DECK_DIRECTORY / TOOLCHAIN_SCRATCH_DIRECTORY,
        work / HOME_DIRECTORY,
        work / OUTPUT_DIRECTORY,
        work / VITE_DIRECTORY,
    ):
        make_open_directory(directory)
    descriptor = os.open(work / STEP_ERROR_FILE, STEP_ERROR_FLAGS, STEP_ERROR_MODE)
    try:
        # The wire user (another uid) reads stderr; a shared group breaks sealing
        os.fchmod(descriptor, STEP_ERROR_MODE)  # NOSONAR(S2612) see #381
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def close_deck(path: Path) -> None:
    """Take the deck back to the root mode once the upload child is gone."""
    path.chmod(ROOT_MODE)
