"""The capabilities the deck list calls out through; adapters fill them.

Named for decks rather than for a catalogue, because the message catalog
is a different thing entirely.
"""

from abc import abstractmethod
from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Protocol

from presentator.contracts.decks import (
    Artefacts,
    Build,
    BuildAttempt,
    BuildFailure,
    BuildRun,
    ConnectionCheckResult,
    Deck,
    DeployKeyDraft,
    RunLeft,
    Source,
    SourcePoll,
    SourceRun,
    SourceWrite,
    ToolchainIdentity,
    ToolchainUnavailable,
)


class SourceStore(Protocol):
    """The sources decks are mirrored from, one row each."""

    @abstractmethod
    def add(self, write: SourceWrite) -> Source | None:
        """Write a new source with its secrets, or nothing when name or URL is taken.

        The access secret is stored encrypted; the webhook secret is stored
        only as the hash. An existing row is not rewritten.
        """

    @abstractmethod
    def add_from_draft(self, write: SourceWrite, *, draft_id: str) -> Source | None:
        """Promote this owner's deploy-key draft into a new source atomically."""

    @abstractmethod
    def hook_secret_hash(self, name: str) -> bytes | None:
        """The stored hash of that source's webhook secret, if this name exists.

        The name is matched against stored names and is never a path.
        """

    @abstractmethod
    def put_credential(self, source_id: str, secret: str) -> None:
        """Replace that source's read-only secret with this value, encrypted.

        Nothing of the value is returned.
        """

    @abstractmethod
    def renew_deploy_key(self, source_id: str) -> bool:
        """Replace one source's generated deploy key, if its row still exists."""

    @abstractmethod
    def put_hook_secret_hash(self, name: str, digest: bytes) -> bool:
        """Replace that source's webhook-secret hash, or nothing when it is missing."""

    @abstractmethod
    def all(self) -> tuple[Source, ...]:
        """Every source this instance mirrors, in no promised order."""

    @abstractmethod
    def remove(self, source_id: str) -> None:
        """Delete that source's own row.

        Its decks and its runs are not this call's: `DeckStore` and
        `SourceRuns` own those tables and are asked for them separately.
        """


class DeployKeyDrafts(Protocol):
    """One admin's own unbound SSH keypair, minted when Add opens.

    Not a source: a draft nobody creates a source from is swept after a day,
    never a half-written row.
    """

    @abstractmethod
    def unconsumed_for(
        self,
        owner_id: str,
        *,
        newer_than: datetime,
    ) -> DeployKeyDraft | None:
        """This admin's own unbound draft, or nothing while it has none this young."""

    @abstractmethod
    def mint(self, owner_id: str, *, at: datetime) -> DeployKeyDraft:
        """Generate a fresh keypair and store it as this admin's own new draft."""

    @abstractmethod
    def get_or_mint(
        self,
        owner_id: str,
        *,
        newer_than: datetime,
        at: datetime,
    ) -> DeployKeyDraft:
        """Reuse one young draft or atomically mint this owner's next draft."""

    @abstractmethod
    def bind(self, draft_id: str, *, owner_id: str) -> DeployKeyDraft | None:
        """Take that draft for a new source, only when this admin owns it.

        Consumes the draft: a second call with the same id answers nothing,
        and neither does an id another admin's draft carries.
        """

    @abstractmethod
    def sweep(self, *, older_than: datetime) -> None:
        """Delete every draft minted before that moment, private key and all."""

    @abstractmethod
    def count_for_owner(self, owner_id: str) -> int:
        """How many drafts that person holds, readable or not."""

    @abstractmethod
    def remove_for_owner(self, owner_id: str) -> int:
        """Delete that person's drafts without opening them; how many went."""


class DeckFolders(Protocol):
    """Reads the folders a source carries, without deciding what a deck is."""

    @abstractmethod
    def folders(self, source: Source) -> SourcePoll:
        """Poll the source for its newest folders, and the commit reached.

        No folder is not emptiness: a source nobody could read carries no
        folders and names why, while a read that found no folder carries an
        empty tuple and says every deck is gone. A read that could not finish
        carries no folders, never fewer: the folders it returns are every
        directory directly under the source's folder at the commit.
        """

    @abstractmethod
    def forget(self, source: Source) -> PurePosixPath | None:
        """Delete this source's mirror from disk, and name it if it stayed.

        A source whose mount or host has since gone away still has a mirror
        to delete; nothing to delete is fine too, so a name asked about twice
        answers nothing calmly. A directory that survives the attempt is
        answered by its path relative to the data volume, so a caller that
        must not go on believing a mirror was cleared can stop and say what
        stayed rather than take that on faith.
        """

    @abstractmethod
    def forget_all_but(self, sources: Iterable[Source]) -> None:
        """Delete every mirror on this disk that none of these sources has.

        A mirror that stays is tried again on the next call.
        """


class ConnectionChecker(Protocol):
    """Probes an unsaved source's URL and secret, writing nothing to disk."""

    @abstractmethod
    def check(self, *, url: str, ref: str, secret: str) -> ConnectionCheckResult:
        """What answered: no failure and the head commit, or which failure and why."""


class SourceRuns(Protocol):
    """Every source's recent polls, bounded to the newest the page reads."""

    @abstractmethod
    def record(self, run: SourceRun) -> None:
        """Add this run and drop older ones of the same source past the bound."""

    @abstractmethod
    def newest(self, source_id: str) -> SourceRun | None:
        """That source's newest run, or nothing while it has never been polled."""

    @abstractmethod
    def recent(self, source_id: str) -> tuple[SourceRun, ...]:
        """That source's newest runs, newest first, no more than the page shows.

        The table itself keeps no more than this bound per source (`record`),
        so this is every run the source has, not only a slice of a longer
        history.
        """

    @abstractmethod
    def remove_for_source(self, source_id: str) -> None:
        """Delete every run this source has ever recorded."""


class DeckStore(Protocol):
    """The decks this instance knows about, keyed by their folder name.

    Reading a row whose stored build names a run that is no single name raises
    `UnplacedBuildError` naming that row's source.
    """

    @abstractmethod
    def put(self, deck: Deck) -> bool:
        """Write what a source carries under this slug, keeping its built talk.

        Taking a deck in must not unpresent it and must not take away its
        downloadable PDF, so what a build wrote moves only by putting a build.

        A folder name is one address for the whole instance, so a name another
        source already carries is refused rather than taken over: the answer
        says whether the slug was this deck's source's to write.
        """

    @abstractmethod
    def put_build(self, slug: str, build: Build) -> None:
        """Make that build the talk this deck delivers, in one write.

        Everything the build is switches at once, so nothing can read half a
        switch: an address that answered from the previous build answers from
        the new one, and never from a mixture of the two. The attempt that led
        to it is cleared by the same write: a talk that stands has nothing left
        to report about how it came about.
        """

    @abstractmethod
    def put_attempt(self, slug: str, attempt: BuildAttempt | None) -> None:
        """Record the build this deck last started, without touching its talk.

        No attempt puts the row back to carrying none.

        What a deck delivers is the build's alone, so a run that says it has
        begun, and a run that says it failed, leave the standing talk and its
        PDF exactly where they are (line 16).
        """

    @abstractmethod
    def get(self, slug: str) -> Deck | None:
        """The deck under that slug, or nothing while no folder carries it."""

    @abstractmethod
    def all(self) -> tuple[Deck, ...]:
        """Every deck whose folder is still there, in no promised order."""

    @abstractmethod
    def mark_removed_except(
        self,
        present: frozenset[str],
        *,
        source_id: str,
        at: datetime,
    ) -> None:
        """Mark that source's decks outside `present` removed, clear it inside.

        A mark is not a delete: the row keeps its identity and its owner, so a
        folder pushed again before the mark ends is the deck it was. A marked
        row keeps the instant it was first marked, the start of its retention.
        One source's folders say nothing about another's, so only the named
        source's decks are read.
        """

    @abstractmethod
    def remove_marked_before(self, source_id: str, *, before: datetime) -> int:
        """Delete that source's decks marked removed at or before `before`.

        This ends a mark: the answer is how many rows went. Their builds stay
        for the sweep, which takes every run no row names any more.
        """

    @abstractmethod
    def for_source(self, source_id: str) -> tuple[Deck, ...]:
        """Every deck row this source has ever carried, marked removed or not.

        Read-only: a folder dropped earlier keeps its row and its built talk
        until its mark ends, so this is the whole set a removal counts and
        cleans disk against, never only the ones the list still shows.
        """

    @abstractmethod
    def remove_for_source(self, source_id: str) -> None:
        """Delete every deck row this source has ever carried, marked removed or not."""


class BuildRunner(Protocol):
    """Builds one deck's folder into the talk that deck delivers.

    Where the folder comes from and which toolchain turns it into a talk is the
    adapter's business; the use case only says which deck is to be built.
    """

    @abstractmethod
    def toolchain_identity(self) -> ToolchainIdentity:
        """Which toolchain builds a deck now, and so what a build records."""

    @abstractmethod
    def build(
        self, deck: Deck, *, source: Source
    ) -> Artefacts | BuildFailure | ToolchainUnavailable:
        """What this deck's commit built into, or why nothing came of it.

        A failure is what a broken deck yields, so the talk that already stands
        keeps standing until a build really produced a new one. The reason
        travels with the answer rather than staying in a log, because it is
        what the deck's page has to show its owner (line 9); a failure with no
        words is one no toolchain reported back about. A build that never
        reached a step is no attempt at the deck and answers ToolchainUnavailable.

        A deck is code, so where a build says it wrote is checked rather than
        trusted before that place becomes an address: artefacts that do not
        both stand under the root fail without words. Artefacts handed back
        are kept where every process serves them from, as one run; a store
        that did not take them fails the build with the `store` reason.
        """

    @abstractmethod
    def kept(self, build: Build) -> Artefacts | None:
        """That build's talk and PDF on this process's disk, fetched where missing.

        Nothing is a run the store does not hold; a store that does not answer
        while this process holds no copy raises `BuildStoreUnavailableError`.
        """

    @abstractmethod
    def read(self, written: Path) -> BinaryIO | None:
        """That file of a build opened for reading, or nothing where it is not one.

        Nothing is what a missing file, a directory, and a link anywhere on the
        way to the file all answer alike: a deck's own code wrote the names, so
        none is followed, and what is handed back is the file that was opened,
        not a path to open again.
        """

    @abstractmethod
    def runs(self) -> frozenset[BuildRun] | None:
        """Every run in the store or on this disk, or nothing where a listing failed.

        Nothing is a store or a root that did not list, or a listing that broke
        off halfway: a caller that deletes the difference to what it keeps must
        delete nothing then.
        """

    @abstractmethod
    def drop_copies_except(self, runs: frozenset[BuildRun]) -> None:
        """Take every run but these off this process's disk, the store untouched.

        Only a process that builds nothing meanwhile may ask, as a run it is
        building stands on its disk under no row yet.
        """

    @abstractmethod
    def remove(self, run: BuildRun) -> RunLeft | None:
        """Take that run out of the store and its whole directory off disk.

        Not only the two files a deck's page ever pointed at: the run's own
        directory carried what the deck's own code wrote alongside them, too.
        Only a directory shaped like a run directly under the root goes. Both
        are tried, whichever refuses. A run that survives the attempt is
        answered by what stayed, so a caller that must not go on believing a
        build was cleared can stop and say what stayed rather than take that
        on faith.
        """


class ToolchainThemes(Protocol):
    """The theme names this instance's toolchain project carries."""

    @abstractmethod
    def names(self) -> tuple[str, ...] | None:
        """Every theme name the toolchain builds with, or nothing while unreadable.

        A deck page reads nothing as no row to show, never as an empty set
        (R3).
        """


class WebhookSecrets(Protocol):
    """Where a source's webhook secret comes from, so the application draws none."""

    @abstractmethod
    def new_secret(self) -> str:
        """A fresh unguessable webhook secret, URL-safe, never drawn twice."""


class RefreshOwnershipLostError(Exception):
    """Another process holds the refresh now, so what this one writes is refused."""


class RefreshOwnershipTimedOutError(Exception):
    """The refresh did not become free within the wait a removal allows."""


class RefreshOwnership(Protocol):
    """Which process takes sources in, builds and removes them, one at a time.

    The holder is the only one whose writes to decks, runs and sources count: a
    holder that lost the right, because its link to the database ended and
    another process took over, is refused at its next write.
    """

    @abstractmethod
    def try_hold(self) -> AbstractContextManager[bool]:
        """Hold the refresh while the block runs, or answer False at once.

        False is another process holding it, or this one failing to take
        ownership within the bound a build has.
        """

    @abstractmethod
    def hold(self) -> AbstractContextManager[None]:
        """Wait for the refresh, then hold it while the block runs.

        Raises `RefreshOwnershipTimedOutError` when it is not free within the
        bound a build has.
        """

    @abstractmethod
    def recover(self) -> None:
        """Check the link that carries the refresh and the presence; replace a dead one.

        Called before each refresh, fetch and removal, never while the refresh
        is held.
        """

    @abstractmethod
    def fenced(self) -> AbstractContextManager[None]:
        """Run the block as the holder, refused with `RefreshOwnershipLostError`.

        No other process takes the refresh over while the block runs, so what it
        reads of the rows and the disk stays true until it ends.
        """

    @abstractmethod
    def toolchain_is_present(self, toolchain: ToolchainIdentity, /) -> bool:
        """Whether a process builds with that toolchain now."""
