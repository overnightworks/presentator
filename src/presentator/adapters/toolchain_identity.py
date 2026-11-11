"""The identity of a toolchain project: what a build of any deck is made with."""

import hashlib
import os
from pathlib import Path, PurePosixPath
from typing import Final

from presentator.contracts.decks import ToolchainIdentity

# What a build never reads, at any depth.
_DEPENDENCIES: Final = "node_modules"
# What a build never reads, at the top of the project only. It is a deny list:
# a file this list does not name moves the identity, so a new file costs one
# extra build and never a stale deck.
_UNREAD_DIRECTORIES: Final = frozenset({"tests"})
_UNREAD_FILES: Final = frozenset({"eslint.config.js", "vitest.config.ts"})
_LENGTH_BYTES: Final = 8


def toolchain_identity_of(project: Path) -> ToolchainIdentity:
    """The SHA-256 of the project's files that a build can read.

    Symbolic links are skipped, not followed. Files are fed in the byte order of
    their POSIX paths relative to the project, each as the length and the path
    as the file system holds it (a name that is not UTF-8 keeps its bytes), then
    the length and the content, so where one file ends and the next begins is
    part of what is hashed. A project that cannot be read raises
    `OSError` naming the path that failed.
    """
    digest = hashlib.sha256()
    for path in sorted(_files_under(project), key=_path_bytes):
        for part in (_path_bytes(path), (project / path).read_bytes()):
            digest.update(len(part).to_bytes(_LENGTH_BYTES, "big"))
            digest.update(part)
    return ToolchainIdentity(digest.hexdigest())


def _path_bytes(path: PurePosixPath) -> bytes:
    """The bytes the file system holds for a path; a name that is not UTF-8 survives."""
    return path.as_posix().encode(errors="surrogateescape")


def _files_under(project: Path) -> list[PurePosixPath]:
    """The regular files a build can read, as paths relative to the project."""
    found: list[PurePosixPath] = []
    pending = [PurePosixPath()]
    while pending:
        directory = pending.pop()
        with os.scandir(project / directory) as entries:
            for entry in entries:
                relative = directory / entry.name
                if entry.is_symlink() or _is_unread(relative, directory=entry.is_dir()):
                    continue
                if entry.is_dir():
                    pending.append(relative)
                elif entry.is_file():
                    found.append(relative)
    return found


def _is_unread(relative: PurePosixPath, *, directory: bool) -> bool:
    """Whether a build never reads this entry, by its path in the project."""
    if directory:
        return (
            relative.name == _DEPENDENCIES or relative.as_posix() in _UNREAD_DIRECTORIES
        )
    return relative.as_posix() in _UNREAD_FILES
