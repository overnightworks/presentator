"""The runs of the build on disk: which exist, which a path belongs to, taking one away.

A run is a directory directly under the builds root that holds an `out`
directory, and a stored talk or PDF belongs to it only at exactly
`<root>/<name>/out/talk` or `<root>/<name>/out/deck.pdf`. Everything here is
anchored to the configured root and names no run from a stored path alone,
because automatic retention deletes what these functions answer for.

Ancestors of the root are the operator's and outside the threat model; below it,
a deck's own code wrote the tree, so no link is followed.
"""

import logging
import os
import shutil
import stat
from contextlib import suppress
from pathlib import Path
from typing import Final

from presentator.contracts.decks import OUTPUT_DIRECTORY, PDF_FILE, TALK_DIRECTORY

# Owner rwx, others read and enter: a run is never left closed to the backup
# that reads the builds volume as another uid, even one that stays half removed.
RUN_DIRECTORY_MODE: Final = 0o755
# A descriptor pinned to a directory nobody may read is still that directory;
# this is the name the running kernel gives it, and the one way its mode can be
# changed without naming a path a build could have exchanged underneath.
_THE_DESCRIPTOR_ITSELF: Final = "/proc/self/fd/{descriptor}"
_HERE: Final = "."
_UP: Final = ".."
_RUN_STAYED: Final = "what build %s left could not be taken away"
_NOT_A_RUN: Final = "%s under the builds root is not a run and is left alone"
_ROOT_NOT_LISTED: Final = "the builds root %s could not be listed: %s"
_REFUSED: Final = "run %s is not shaped like a run and is not removed"

_log = logging.getLogger(__name__)


def run_name_of(root: Path, stored: Path) -> str | None:
    """The run that stored talk or PDF path belongs to, by its exact shape."""
    path = stored.absolute()
    run_directory = path.parent.parent
    if (
        path.name not in {TALK_DIRECTORY, PDF_FILE}
        or path.parent.name != OUTPUT_DIRECTORY
        or run_directory.parent != root
        or _UP in path.parts
    ):
        return None
    return run_directory.name


def listed_runs(root: Path) -> frozenset[str] | None:
    """Every run directly under that root, or nothing where the listing broke off."""
    try:
        with os.scandir(root) as entries:
            names = [entry.name for entry in entries]
    except OSError as problem:
        _log.warning(_ROOT_NOT_LISTED, root, problem)
        return None
    runs = {name for name in names if _is_a_run(root / name)}
    for name in sorted(set(names) - runs):
        _log.warning(_NOT_A_RUN, name)
    return frozenset(runs)


def removed_run(root: Path, name: str) -> bool:
    """Take that run off disk if it is one, and say whether it is gone."""
    directory = root / name
    if not _still_stands(directory):
        return True
    if not _is_a_run(directory):
        _log.warning(_REFUSED, name)
        return False
    return taken_away(directory)


def _still_stands(directory: Path) -> bool:
    """Whether the directory is still on disk; only a confirmed absence says no.

    A directory that cannot be looked at (a closed parent, an I/O error) is not
    known to be gone, so it counts as standing rather than as removed.
    """
    try:
        directory.lstat()
    except FileNotFoundError, NotADirectoryError:
        return False
    except OSError:
        return True
    return True


def _is_a_run(directory: Path) -> bool:
    """Whether this is a real directory holding a real `out` directory."""
    try:
        return stat.S_ISDIR(directory.lstat().st_mode) and stat.S_ISDIR(
            (directory / OUTPUT_DIRECTORY).lstat().st_mode,
        )
    except OSError:
        return False


def taken_away(directory: Path) -> bool:
    """Take away everything one run's directory holds, however it got there.

    Called both for a build that did not finish, where nothing points at its
    directory and nothing ever will, and for one that did and is now removed on
    purpose. A deck's own code wrote in this tree and may have closed a
    directory behind it, so the modes are given back first. Whether the tree
    really went is then read off the filesystem, and one that stayed is named
    in the log and answered here as well, so a caller that must know can act on
    it rather than believe a removal that did not happen.
    """
    _opened_again(directory)
    shutil.rmtree(directory, ignore_errors=True)
    if _still_stands(directory):
        _log.warning(_RUN_STAYED, directory.name)
        return False
    return True


def _opened_again(tree: Path) -> None:
    """Give back the modes a build may have taken from its own leftovers.

    Every directory is pinned as a descriptor before anything is done to it,
    and the mode is changed through that descriptor rather than through the
    name it had, which a build may since have made into a link to anything of
    this machine's. A directory a build closed cannot be opened for reading at
    all, so it is pinned without reading it and opened again through itself.
    """
    pinned = _pinned(tree)
    while pinned:
        holder = pinned.pop()
        try:
            _let_the_owner_back_into(holder, and_pin=pinned)
        finally:
            os.close(holder)


def _let_the_owner_back_into(holder: int, *, and_pin: list[int]) -> None:
    """Open that one directory to its owner and others, and pin the ones in it."""
    with suppress(OSError):
        # A descriptor pinned without reading rights cannot be changed
        # directly; this is where the running kernel lets its owner name it.
        Path(_THE_DESCRIPTOR_ITSELF.format(descriptor=holder)).chmod(RUN_DIRECTORY_MODE)
        readable = os.open(_HERE, os.O_RDONLY | os.O_DIRECTORY, dir_fd=holder)
        try:
            _pin_the_directories_in(readable, and_pin=and_pin)
        finally:
            os.close(readable)


def _pin_the_directories_in(readable: int, *, and_pin: list[int]) -> None:
    """Pin every directory of that one, and no name that is not one.

    Each is pinned as it is found, so a listing that breaks off halfway still
    leaves the caller holding what it has to let go of again.
    """
    with suppress(OSError):
        for entry in os.scandir(readable):
            if entry.is_dir(follow_symlinks=False):
                with suppress(OSError):
                    and_pin.append(
                        os.open(
                            entry.name,
                            os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=readable,
                        ),
                    )


def _pinned(tree: Path) -> list[int]:
    """That tree's own directory as a descriptor, where it still is one."""
    try:
        return [os.open(tree, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)]
    except OSError:
        return []
