"""The build worker's scratch places: where a step writes and a start looks."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    TOOLCHAIN_SCRATCH_DIRECTORY,
)
from presentator.host.worker_wire import STEP_ERROR_FILE

ROOT_MODE = 0o755
WORK_MOUNT = "/work"
# Mount points inside the build life; each is a private tmpfs of the build container.
TEMPORARY_MOUNT = "/tmp"  # NOSONAR(S5443) # noqa: S108 - a private tmpfs of the build container and not a shared host directory
SHARED_MEMORY_MOUNT = "/dev/shm"  # NOSONAR(S5443) # noqa: S108 - a private tmpfs of the build container and not a shared host directory
QUEUE_MOUNT = "/dev/mqueue"  # NOSONAR(S5443) only listed, never written
HOME_DIRECTORY = "home"
VITE_DIRECTORY = "vite"
SCRATCH_ROOTS = (WORK_MOUNT, TEMPORARY_MOUNT, SHARED_MEMORY_MOUNT, QUEUE_MOUNT)
# The kernel's SysV tables and the name of the id column its header line holds.
SYSV_ID_COLUMNS = {"shm": "shmid", "msg": "msqid", "sem": "semid"}
# Longer than the headers (85 to 170 bytes), so a row is read whenever one exists.
SYSV_READ_BYTES = 4096
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


@dataclass(frozen=True)
class Scratch:
    """What a start finds in the scratch places."""

    occupied_roots: frozenset[str]
    sysv_tables: Mapping[str, str]


def _holds_an_entry(directory: Path) -> bool:
    with os.scandir(directory) as entries:
        return next(entries, None) is not None


def _read_sysv_table(root: Path, table: str) -> str:
    with (root / "proc/sysvipc" / table).open("rb") as listing:
        return listing.read(SYSV_READ_BYTES).decode()


def read_scratch(root: Path) -> Scratch:
    """List each scratch root up to its first entry and read each SysV table.

    Nothing below a root is opened, so a closed directory is refused by its name.
    Raises OSError or UnicodeDecodeError on a failed read.
    """
    return Scratch(
        occupied_roots=frozenset(
            mount
            for mount in SCRATCH_ROOTS
            if _holds_an_entry(root / mount.lstrip("/"))
        ),
        sysv_tables={table: _read_sysv_table(root, table) for table in SYSV_ID_COLUMNS},
    )


def _is_header_line(table: str, text: str) -> bool:
    lines = text.splitlines()
    return (
        text.endswith("\n")
        and len(lines) == 1
        and lines[0].split()[:2] == ["key", SYSV_ID_COLUMNS[table]]
    )


def scratch_empty(scratch: Scratch) -> bool:
    """Whether no root holds an entry and each SysV table is only its header."""
    return not scratch.occupied_roots and all(
        _is_header_line(table, scratch.sysv_tables.get(table, ""))
        for table in SYSV_ID_COLUMNS
    )
