"""Building again the decks whose commit changed, and what each attempt records.

For each deck of a source the walk decides whether its folder's commit needs a
build, and what the deck's attempt says about it. The deck store, the build
runner and the clock stay outside.
"""

from dataclasses import dataclass
from datetime import timedelta

from presentator.application import build_retention
from presentator.contracts.decks import (
    Artefacts,
    Build,
    BuildAttempt,
    BuildFailure,
    BuildOutcome,
    Deck,
    Source,
    SourceFetch,
    ToolchainUnavailable,
)
from presentator.ports.clock import Clock
from presentator.ports.decks import BuildRunner, DeckStore


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


@dataclass(frozen=True, slots=True, kw_only=True)
class DeckBuilds:
    """The build walk of one instance, over its store, build runner, clock and bound."""

    store: DeckStore
    builder: BuildRunner
    clock: Clock
    build_bound: timedelta

    def build_what_changed(self, source: Source, *, building: bool) -> BuildWalk:
        """Build that source's decks whose commit nothing has tried yet.

        The commit is the change check: a push moves one folder's commit, so only that
        deck is rebuilt. A folder at a standing talk's commit whose run is on disk needs
        no build. A tried commit is not retried, so a deck that cannot build costs one
        build, not one per refresh. A vanished run is rebuilt: `builds/` and `mirrors/`
        are caches the rows rebuild; a gone mirror is cloned again at the next take-in.
        Answers, per build this pass started, whether it delivered the deck, and
        whether building may go on: a build that met a toolchain that was not there
        ends it, and the caller passes `building=False` to the sources after.
        """
        delivered: list[bool] = []
        for stored in self.store.all():
            if stored.source_id != source.id:
                continue
            deck = self._give_up_on_a_build_that_never_returned(stored)
            standing = build_retention.standing_build(self.builder, deck)
            if standing is not None and standing.commit == deck.commit:
                self._deliver_the_standing_talk_again(deck, standing)
                continue
            if building and not self._was_already_tried(deck):
                built = self._switch_over(deck, source)
                delivered.append(bool(built))
                building = built is not None
        return BuildWalk(delivered=tuple(delivered), still_building=building)

    def _deliver_the_standing_talk_again(self, deck: Deck, standing: Build) -> None:
        """Make the talk that stands this commit's talk again, building nothing.

        Reverting a broken push is how a person undoes it in git (line 16): the
        folder is back at the commit the standing talk was built from, so that
        talk is current again and the failure recorded against the push that
        was reverted goes with the one statement a successful switch writes.
        Its build time does not move, because that build is the one that ran.
        A deck that carries no attempt is left alone, so an unchanged deck
        costs a refresh no write at all.
        """
        if deck.attempt is None:
            return
        self.store.put_build(deck.slug, standing)

    def _was_already_tried(self, deck: Deck) -> bool:
        """Whether a build was already started for the commit the folder carries."""
        return deck.attempt is not None and deck.attempt.commit == deck.commit

    def _give_up_on_a_build_that_never_returned(self, deck: Deck) -> Deck:
        """Call an attempt that outlived the build bound failed, not running.

        Builds follow one another inside one refresh, so an attempt still
        saying it runs when the next refresh reads it belongs to a process that
        is gone — the server was restarted while it built. Nobody may read that
        as a deck building for ever, and it left no words to show. The bound is
        one toolchain step's, not the whole walk's, on purpose: no live build
        is ever read here, so the bound only has to be too long for a leftover
        to be mistaken for one that just began.
        """
        attempt = deck.attempt
        if attempt is None or attempt.outcome is not BuildOutcome.RUNNING:
            return deck
        if self.clock.now() - attempt.started_at <= self.build_bound:
            return deck
        failed = self._failed(deck, attempt, BuildFailure.without_words())
        return Deck(
            slug=deck.slug,
            title=deck.title,
            changed_at=deck.changed_at,
            owner_id=deck.owner_id,
            source_id=deck.source_id,
            commit=deck.commit,
            build=deck.build,
            attempt=failed,
        )

    def _switch_over(self, deck: Deck, source: Source) -> bool | None:
        """Deliver this deck from its new build, once there really is one.

        The attempt is recorded before the toolchain starts, so a page opened
        while it runs says so, and a run that dies with the server leaves a
        record behind rather than nothing. A build that failed, and one that
        left artefacts anywhere but under the root they belong in, write no
        pointer at all: whatever the deck was delivering before keeps being
        delivered, and the attempt says why. A toolchain that was not there makes
        the build no attempt: the attempt the deck carried is put back. Answers
        whether it was delivered, or None when the toolchain was not there.
        """
        attempt = BuildAttempt(
            commit=deck.commit,
            started_at=self.clock.now(),
            outcome=BuildOutcome.RUNNING,
            failure=None,
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
        self.store.put_build(
            deck.slug,
            Build(
                directory=built.directory,
                pdf=built.pdf,
                commit=deck.commit,
                built_at=self.clock.now(),
            ),
        )
        return True

    def _failed(
        self, deck: Deck, attempt: BuildAttempt, failure: BuildFailure
    ) -> BuildAttempt:
        """Close this attempt as failed, keeping the moment it started."""
        closed = BuildAttempt(
            commit=attempt.commit,
            started_at=attempt.started_at,
            outcome=BuildOutcome.FAILED,
            failure=failure,
        )
        self.store.put_attempt(deck.slug, closed)
        return closed
