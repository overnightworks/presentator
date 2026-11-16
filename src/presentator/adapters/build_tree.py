"""Looking inside what a deck's build wrote without ever following a link it made.

A build's own code fills these directories, so every name in them is read
through a descriptor opened from the one holding it, never by its path.
"""

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Final

from presentator.adapters.build_wire import MAXIMUM_ENTRIES

_UP: Final = ".."
_A_MEGABYTE: Final = 1024 * 1024


@dataclass(frozen=True, slots=True, kw_only=True)
class Added:
    """What adding up one run's talk found, and what stopped the adding up.

    A pass that was stopped carries no size: it says how far it came, and the
    caller refuses the build rather than believing a number nobody counted.
    """

    entries: int
    megabytes: int = 0
    unreadable: str | None = None
    in_time: bool = True


def added_up(directory: Path, *, ends_by: float) -> Added:
    """Add up what one run has written into that directory, so far.

    Every name in it was written by a deck's own build, so nothing here is
    reached by its path: the root is opened once, and every directory inside
    it is opened from the descriptor of the one that holds it, as a directory
    and never through a link. A name that is not a real directory by the time
    it is opened is refused rather than followed — between one look and the
    next a build can make a name into anything, and a descriptor is the one
    thing it cannot exchange. A link is counted as the name it is, the walk
    stops at the first entry it cannot read, and it stops at the entry and at
    the moment it may not go past: a talk this server could not add up is one
    it refuses, never one it guesses at.
    """
    root = _a_directory_of_its_own(directory)
    if root is None:
        return Added(entries=0, unreadable=str(directory))
    still_open = [root]
    try:
        return _walked(still_open, ends_by=ends_by)
    finally:
        for holder in still_open:
            os.close(holder)


def _walked(still_open: list[int], *, ends_by: float) -> Added:
    held, entries = 0, 0
    while still_open:
        holder = still_open.pop()
        try:
            with os.scandir(holder) as listing:
                for entry in listing:
                    entries += 1
                    stopped = _stopped_at(entries, ends_by=ends_by)
                    if stopped is not None:
                        return stopped
                    held += _size_of(entry, held_by=holder, still_open=still_open)
        except OSError as unreadable:
            return Added(entries=entries, unreadable=str(unreadable))
        finally:
            os.close(holder)
    return Added(entries=entries, megabytes=held // _A_MEGABYTE)


def _stopped_at(entries: int, *, ends_by: float) -> Added | None:
    if entries > MAXIMUM_ENTRIES:
        return Added(entries=entries)
    if monotonic() > ends_by:
        return Added(entries=entries, in_time=False)
    return None


def _size_of(entry: os.DirEntry[str], *, held_by: int, still_open: list[int]) -> int:
    if entry.is_dir(follow_symlinks=False):
        still_open.append(opened_within(entry.name, held_by=held_by))
        return 0
    return 0 if entry.is_symlink() else entry.stat(follow_symlinks=False).st_size


def opened_within(name: str, *, held_by: int) -> int:
    """That name as a directory of its own, opened from the one holding it.

    The open is the whole check: it refuses a link and it refuses anything
    that is not a directory, at the moment it happens, so nothing a build does
    in between can turn the name it read into a way out of this run.
    """
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=held_by,
    )


def opened_without_a_link(
    written: Path,
    *,
    under: Path,
    how: int = os.O_RDONLY,
) -> int | None:
    """That path as a descriptor, if it is a regular file and no name on it a link.

    The root is this deployment's own and opened as it is configured; from
    there on each directory is opened from the descriptor of the one holding
    it, and the last name is looked at itself rather than at what it may point
    to. A name that is not a regular file is closed again, and never read: it
    is opened without waiting, so a pipe a build left cannot hold a request.
    """
    if not written.is_relative_to(under):
        return None
    names = written.relative_to(under).parts
    if not names or _UP in names:
        return None
    *directories, name = names
    try:
        holder = os.open(under, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for directory in directories:
            inner = opened_within(directory, held_by=holder)
            os.close(holder)
            holder = inner
        found = os.open(name, how | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=holder)
    except OSError, ValueError:
        return None
    finally:
        os.close(holder)
    if stat.S_ISREG(os.fstat(found).st_mode):
        return found
    os.close(found)
    return None


def _a_directory_of_its_own(directory: Path) -> int | None:
    """That directory as a descriptor, or nothing where it is not one."""
    try:
        return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None
