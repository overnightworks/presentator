"""The runs of the build on this process's disk, a cache of the object store.

A run is kept in the store as soon as it was built, so every process serves it:
the one that built it from the directory the build left, any other from a copy
it fetches into the same place on its first request. A talk a process holds
keeps being served while the store is down. The cache is bounded: past its
bytes, the runs served least recently go first, and only runs the store still
holds, so a build under way here is never taken. A run that went is fetched
again when it is asked for.
"""

import os
import tarfile
from abc import abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Final, Protocol

from presentator.adapters.build_runs import (
    RUN_DIRECTORY_MODE,
    listed_runs,
    removed_run,
    taken_away,
)
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    MANIFEST_FILE,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    SLIDES_FILE,
    TALK_DIRECTORY,
)

# Written into the run's own directory while it travels, and gone after.
_ARCHIVE: Final = "talk.tar"
# What of the deck's own tree belongs to the run: the words a voice reads.
_DECK_TEXTS: Final = (SLIDES_FILE, MANIFEST_FILE)


class RunObjects(Protocol):
    """Where the runs are kept for every process: an archive and a PDF each."""

    @abstractmethod
    def put(self, run: str, *, archive: Path, pdf: Path) -> bool:
        """Keep that run's archive and PDF, and say whether both were written."""

    @abstractmethod
    def fetch(self, run: str, *, archive: Path, pdf: Path) -> bool:
        """Write that run's archive and PDF to these files, or say it could not."""

    @abstractmethod
    def names(self) -> frozenset[str] | None:
        """The name of every run kept, or nothing where the listing failed."""

    @abstractmethod
    def delete(self, run: str) -> bool:
        """Delete that run's objects, and say whether they went."""


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildCache:
    """The runs under the builds root, each kept in the store and fetched from it."""

    root: Path
    store: RunObjects
    # Past this many bytes on disk, the runs served least recently go.
    bound_bytes: int

    def keep(self, name: str) -> bool:
        """Put the run this process built into the store, and say whether it is there.

        Only the talk, the PDF and the deck's text travel, and of the talk only
        directories and regular files: a link a deck's code left is no file of
        its talk, and serving refuses it anyway. A run the store took counts
        against the bound like one fetched.
        """
        run = self.root / name
        archive = run / _ARCHIVE
        try:
            with tarfile.open(archive, "w") as packed:
                for member in _members_of(run):
                    packed.add(run / member, arcname=str(member), recursive=False)
            kept = self.store.put(
                name, archive=archive, pdf=run / OUTPUT_DIRECTORY / PDF_FILE
            )
        except OSError:
            return False
        finally:
            archive.unlink(missing_ok=True)
        if kept:
            self._bounded(keeping=name)
        return kept

    def local(self, name: str) -> Path | None:
        """That run's directory on this disk, fetched first where it is not here.

        Asking counts as serving it, so it is the last the bound takes. Nothing
        while the store holds no such run or does not answer.
        """
        run = self.root / name
        if not run.is_dir() and not self._fetched(name):
            return None
        with suppress(OSError):
            # A run half taken away still answers what it has; serving says the rest.
            os.utime(run / OUTPUT_DIRECTORY)
        return run

    def names(self) -> frozenset[str] | None:
        """Every run in the store or on this disk, or nothing where one is unlisted."""
        stored = self.store.names()
        here = listed_runs(self.root.absolute())
        if stored is None or here is None:
            return None
        return stored | here

    def drop_copies_except(self, names: frozenset[str]) -> None:
        """Take every run but these off this disk; a root never made holds none."""
        root = self.root.absolute()
        here = listed_runs(root) if root.is_dir() else None
        for name in (here or frozenset()) - names:
            removed_run(root, name)

    def remove(self, name: str) -> bool:
        """Take that run out of the store and off this disk, and say whether it went."""
        return self.store.delete(name) and removed_run(self.root.absolute(), name)

    def _fetched(self, name: str) -> bool:
        """Fetch that run into a directory of its own, then move it into place at once.

        Two requests that fetch one run at the same moment each write their own
        copy; the first to arrive stands, and the other is taken away.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        arriving = Path(mkdtemp(dir=self.root))
        try:
            (arriving / OUTPUT_DIRECTORY).mkdir()
            archive = arriving / _ARCHIVE
            if not self.store.fetch(
                name, archive=archive, pdf=arriving / OUTPUT_DIRECTORY / PDF_FILE
            ):
                return False
            with tarfile.open(archive) as packed:
                packed.extractall(arriving, filter="data")
            archive.unlink()
            arriving.chmod(RUN_DIRECTORY_MODE)
            arriving.rename(self.root / name)
        except OSError, tarfile.TarError:
            return self.root.joinpath(name).is_dir()
        finally:
            if arriving.exists():
                taken_away(arriving)
        self._bounded(keeping=name)
        return True

    def _bounded(self, *, keeping: str) -> None:
        """Take the runs served least recently off disk until the rest fit the bound."""
        here, stored = listed_runs(self.root.absolute()), self.store.names()
        if here is None or stored is None:
            return
        sizes = {name: _bytes_of(self.root / name) for name in here}
        total = sum(sizes.values())
        for name in sorted(
            (here & stored) - {keeping},
            key=lambda each: _served_at(self.root / each),
        ):
            if total <= self.bound_bytes:
                return
            if removed_run(self.root.absolute(), name):
                total -= sizes[name]


def _members_of(run: Path) -> list[Path]:
    """What of a run travels, relative to it: its talk, then the deck's text."""
    talk = Path(OUTPUT_DIRECTORY, TALK_DIRECTORY)
    members = [talk]
    for directory, folders, files in os.walk(run / talk):
        here = Path(directory).relative_to(run)
        members += [
            here / name
            for name in (*folders, *files)
            if not (run / here / name).is_symlink()
        ]
    members += [
        Path(DECK_DIRECTORY, name)
        for name in _DECK_TEXTS
        if _is_a_regular_file(run / DECK_DIRECTORY / name)
    ]
    return members


def _is_a_regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _bytes_of(run: Path) -> int:
    """What that run's tree holds, its links not followed."""
    return sum(
        (Path(directory) / name).lstat().st_size
        for directory, _, files in os.walk(run)
        for name in files
    )


def _served_at(run: Path) -> float:
    try:
        return (run / OUTPUT_DIRECTORY).stat().st_mtime
    except OSError:
        return 0.0
