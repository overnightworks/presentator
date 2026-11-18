"""The object store of the runs as a directory a test owns.

The real store is Garage, which CI starts; this double keeps each run's archive
and PDF as two files under a directory of their own, for tests whose subject is
not the store.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Final

# Room for every talk a test lays down; the bound is proven on its own.
A_CACHE_BOUND: Final = 1024**3
_ARCHIVE: Final = "talk.tar"
_PDF: Final = "deck.pdf"


@dataclass
class DirectoryRunObjects:
    """Each run as a directory holding its archive and its PDF."""

    directory: Path
    # Whether the store answers at all, the way one that is down does not.
    answering: bool = True

    def put(self, run: str, *, archive: Path, pdf: Path) -> bool:
        if not self.answering:
            return False
        kept = self.directory / run
        kept.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, kept / _PDF)
        shutil.copyfile(archive, kept / _ARCHIVE)
        return True

    def fetch(self, run: str, *, archive: Path, pdf: Path) -> bool:
        kept = self.directory / run
        if not self.answering or not (kept / _ARCHIVE).is_file():
            return False
        shutil.copyfile(kept / _ARCHIVE, archive)
        shutil.copyfile(kept / _PDF, pdf)
        return True

    def names(self) -> frozenset[str] | None:
        if not self.answering:
            return None
        if not self.directory.is_dir():
            return frozenset()
        return frozenset(entry.name for entry in self.directory.iterdir())

    def delete(self, run: str) -> bool:
        if not self.answering:
            return False
        shutil.rmtree(self.directory / run, ignore_errors=True)
        return True
