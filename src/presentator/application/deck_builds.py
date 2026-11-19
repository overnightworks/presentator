"""Building again the decks whose commit or toolchain changed, and what attempts say.

For each deck of a source the walk decides whether its folder's commit and the
toolchain that builds now need a build, and what the deck's attempt says about
it. The deck store, the build runner and the clock stay outside.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from presentator.application import build_retention
from presentator.contracts.decks import (
    Artefacts,
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    Deck,
    Source,
    SourceFetch,
    ToolchainIdentity,
    ToolchainUnavailable,
)
from presentator.ports.clock import Clock
from presentator.ports.decks import BuildRunner, DeckStore

_log = logging.getLogger(__name__)

_REFRESH_COMPLETED: Final = "refresh.completed"
_REFRESH_REFUSED: Final = "refresh.refused"
_OWNERSHIP_LOST: Final = "ownership_lost"


def log_refresh(fetches: Sequence[SourceFetch]) -> None:
    """Write the refresh's one line with its counts, or none where nothing changed."""
    counts = {
        "decks_built_count": sum(fetch.decks_built for fetch in fetches),
        "builds_failed_count": sum(fetch.builds_failed for fetch in fetches),
        "decks_removed_count": sum(fetch.decks_removed for fetch in fetches),
    }
    if any(counts.values()):
        _log.info(
            _REFRESH_COMPLETED, extra={"sources_fetched_count": len(fetches), **counts}
        )


def log_refresh_ownership_lost() -> None:
    """Say a refresh stopped because another process took it over."""
    _log.info(_REFRESH_REFUSED, extra={"reason": _OWNERSHIP_LOST})


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildWalk:
    """One source's walk: what it delivered, and whether building may go on."""

    delivered: tuple[bool, ...]
    still_building: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class TakenIn:
    """What taking one source in fetched, and whether building may go on after it."""

    fetch: SourceFetch
    still_building: bool


def _made_for(
    made: Build | BuildAttempt, deck: Deck, toolchain: ToolchainIdentity
) -> bool:
    """Whether a build or an attempt was made for this commit under this toolchain.

    A build or attempt with no recorded toolchain was made for none, so it never is.
    """
    return (made.commit, made.toolchain) == (deck.commit, toolchain)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeckBuilds:
    """The build walk of one instance, over its store, build runner and clock."""

    store: DeckStore
    builder: BuildRunner
    clock: Clock
    # Whether a process of that toolchain runs now, in this instance or another.
    toolchain_is_present: Callable[[ToolchainIdentity], bool]

    def build_what_changed(self, source: Source, *, building: bool) -> BuildWalk:
        """Build that source's decks whose commit and toolchain nothing has tried yet.

        The commit and the toolchain the runner answers, read once before the first
        deck is judged, are the change check: a push moves one folder's commit, so
        only that deck is rebuilt, and a toolchain change moves every deck's. A
        folder at a standing talk's commit and toolchain whose run is kept needs
        no build. A commit and toolchain tried are not tried again, so a deck that
        cannot build costs one build, not one per refresh. A commit another
        toolchain built or tried is left to it while a process of that toolchain
        runs, so a rollout and a rollback never rebuild each other's decks; once
        none runs, this toolchain builds it. A vanished run is rebuilt: the store
        and `mirrors/` are caches the rows rebuild; a gone mirror is cloned again
        at the next take-in. Answers, per build this pass started, whether it
        delivered the deck, and whether building may go on: a build that met a
        toolchain or a store that was not there ends it, and the caller passes
        `building=False` to the sources after.
        """
        toolchain = self.builder.toolchain_identity()
        delivered: list[bool] = []
        for stored in self.store.all():
            if stored.source_id != source.id:
                continue
            deck = self._fail_an_orphaned_attempt(stored)
            standing = build_retention.standing_build(deck, self.builder.runs())
            if standing is not None and _made_for(standing, deck, toolchain):
                self._deliver_the_standing_talk_again(deck, standing)
                continue
            if self._is_left_to_a_present_toolchain(deck, standing, toolchain):
                continue
            if building and not self._was_already_tried(deck, toolchain):
                built = self._switch_over(deck, source, toolchain)
                delivered.append(bool(built))
                building = built is not None
        return BuildWalk(delivered=tuple(delivered), still_building=building)

    def _deliver_the_standing_talk_again(self, deck: Deck, standing: Build) -> None:
        """Make the talk that stands this commit's talk again, building nothing.

        Reverting a broken push is how a person undoes it in git (line 16): the
        folder is back at the commit the standing talk was built from, under the
        toolchain that builds now, so that talk is current again and the failure
        recorded against the push that was reverted goes with the one statement a
        successful switch writes.
        Its build time does not move, because that build is the one that ran.
        A deck that carries no attempt is left alone, so an unchanged deck
        costs a refresh no write at all.
        """
        if deck.attempt is None:
            return
        self.store.put_build(deck.slug, standing)

    def _was_already_tried(self, deck: Deck, toolchain: ToolchainIdentity) -> bool:
        """Whether a build was already started for this commit under this toolchain.

        One the store did not take says nothing about the commit, so it is tried again.
        """
        attempt = deck.attempt
        return (
            attempt is not None
            and _made_for(attempt, deck, toolchain)
            and (
                attempt.failure is None
                or attempt.failure.reason is not BuildFailureReason.STORE
            )
        )

    def _is_left_to_a_present_toolchain(
        self, deck: Deck, standing: Build | None, toolchain: ToolchainIdentity
    ) -> bool:
        """Whether a toolchain a process still runs built or tried this commit."""
        return any(
            made is not None
            and made.commit == deck.commit
            and made.toolchain is not None
            and made.toolchain != toolchain
            and self.toolchain_is_present(made.toolchain)
            for made in (standing, deck.attempt)
        )

    def _fail_an_orphaned_attempt(self, deck: Deck) -> Deck:
        """Call an attempt that still says it runs failed: the holder builds alone.

        Builds follow one another inside the one refresh that holds the lock, so
        an attempt the walk reads as running belongs to a holder that is gone,
        however recently it began. Nobody may read that as a deck building for
        ever, and it left no words to show.
        """
        attempt = deck.attempt
        if attempt is None or attempt.outcome is not BuildOutcome.RUNNING:
            return deck
        failed = self._failed(deck, attempt, BuildFailure.without_words())
        return deck.carrying(failed)

    def _switch_over(
        self, deck: Deck, source: Source, toolchain: ToolchainIdentity
    ) -> bool | None:
        """Deliver this deck from its new build, once there really is one.

        The attempt is recorded before the toolchain starts, so a page opened
        while it runs says so, and a run that dies with the server leaves a
        record behind rather than nothing. A build that failed, and one that
        left artefacts anywhere but under the root they belong in, write no
        pointer at all: whatever the deck was delivering before keeps being
        delivered, and the attempt says why. A toolchain that was not there
        makes the build no attempt: the attempt the deck carried is put back. A
        store that did not take the build ends the attempt failed, naming the
        store. Either way the next refresh tries again.
        The build is kept in the store before its row names it, outside any
        transaction; only that row write is fenced. Answers whether it was
        delivered, or None when the toolchain or the store was not there.
        """
        attempt = BuildAttempt(
            commit=deck.commit,
            started_at=self.clock.now(),
            outcome=BuildOutcome.RUNNING,
            failure=None,
            toolchain=toolchain,
        )
        self.store.put_attempt(deck.slug, attempt)
        built = self.builder.build(deck, source=source)
        if isinstance(built, ToolchainUnavailable):
            self.store.put_attempt(deck.slug, deck.attempt)
            return None
        if not isinstance(built, Artefacts):
            self._failed(deck, attempt, built)
            return False
        if not self.builder.holds(built):
            self._failed(deck, attempt, BuildFailure.without_words())
            return False
        run = self.builder.deliver(built)
        if run is None:
            self._failed(deck, attempt, BuildFailure(reason=BuildFailureReason.STORE))
            return None
        self.store.put_build(
            deck.slug,
            Build(
                run=run,
                commit=deck.commit,
                built_at=self.clock.now(),
                toolchain=toolchain,
            ),
        )
        return True

    def _failed(
        self, deck: Deck, attempt: BuildAttempt, failure: BuildFailure
    ) -> BuildAttempt:
        """Close this attempt as failed, keeping the moment it started."""
        closed = attempt.failed_with(failure)
        self.store.put_attempt(deck.slug, closed)
        return closed
