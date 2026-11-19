"""What a confirmed source removal answers when it deleted no row."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from presentator.contracts.decks import RunLeft, SourceRemoval


class SourceRemovalRefusal(StrEnum):
    """Why a confirmed removal did not delete anything."""

    # The mirror or a built directory survived the attempt to take it away;
    # disk goes first, so no row is touched while either still stands.
    DISK_LEFTOVER = "disk-leftover"
    # The store kept a run's objects; no row is touched while they stand.
    STORE_UNAVAILABLE = "store-unavailable"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRemovalRefused:
    """A removal that deleted no row, and what stayed.

    `builds` are only directories still on disk: a run the store kept is the
    reason, never a directory.
    """

    reason: SourceRemovalRefusal
    mirror: PurePosixPath | None
    builds: tuple[PurePosixPath, ...]


def refusal_of(
    mirror: PurePosixPath | None, runs: tuple[RunLeft, ...]
) -> SourceRemovalRefused | None:
    """What a removal that left this mirror and these runs refuses with, if any."""
    if mirror is None and not runs:
        return None
    reason = (
        SourceRemovalRefusal.STORE_UNAVAILABLE
        if any(run.in_store for run in runs)
        else SourceRemovalRefusal.DISK_LEFTOVER
    )
    return SourceRemovalRefused(
        reason=reason,
        mirror=mirror,
        builds=tuple(run.directory for run in runs if run.directory is not None),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRemovalStale:
    """Moved counts: nothing deleted, so no earlier yes covers more than it saw."""

    current: SourceRemoval
