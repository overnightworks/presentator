"""Taking decks in from a source, building them, and what the lobby then shows.

Which folder counts as a deck, which deck is out of date, when a build may
become the talk that is delivered, and what a deck page says is decided here;
git, TOML, SQL, and the build toolchain stay outside.
"""

import hmac
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from functools import partial
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import BinaryIO, Final

from presentator.application import build_retention
from presentator.application.deck_builds import DeckBuilds
from presentator.application.source_state import (
    SHORT_COMMIT,
    deck_state_of,
    shown_run,
    source_state_of,
)
from presentator.contracts.decks import (
    SLIDES_FILE,
    AccessKind,
    BuildAttempt,
    ConnectionCheckResult,
    Deck,
    DeckFolder,
    DeckPage,
    DeployKeyDraft,
    ListedDeck,
    ListedSource,
    ShownAttempt,
    Source,
    SourceDeck,
    SourceFetch,
    SourcePage,
    SourcePoll,
    SourceRemoval,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
    SourceWrite,
    access_kind_of,
    is_a_folder_path,
    is_a_plain_folder_name,
    normalized_folder,
)
from presentator.ports.clock import Clock
from presentator.ports.decks import (
    BuildRunner,
    ConnectionChecker,
    DeckFolders,
    DeckStore,
    DeployKeyDrafts,
    LocalMount,
    SourceRuns,
    SourceStore,
    ToolchainThemes,
    WebhookSecrets,
)

_log = logging.getLogger(__name__)

_NOT_A_FOLDERS_OWN_NAME: Final = "folder %r is not a folder's own name, skipped"
_NAME_BELONGS_TO_ANOTHER_SOURCE: Final = (
    "folder %r is already carried by another source, skipped for source %s"
)
# A source name is one path element of the webhook address, never a path
# itself: lowercase, digits, hyphens, starting with a letter or digit.
_SOURCE_NAME: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# The form lives at this path segment, so a source must not take it.
RESERVED_SOURCE_NAME: Final = "new"
# Added sources follow main; a later slice is what offers another ref.
_ADDED_REF: Final = "main"
_HASH_LENGTH: Final = 32
_NO_HASH: Final = bytes(_HASH_LENGTH)
# A draft nobody creates a source from is discarded after this long (operator
# ruling 07.09.2026), and reused on every Add opened before then.
DRAFT_MAX_AGE: Final = timedelta(days=1)


class SourceRefusal(StrEnum):
    """Why adding a source did not write a row, in the form's own cases."""

    MALFORMED_NAME = "malformed-name"
    MALFORMED_FOLDER = "malformed-folder"
    DUPLICATE_NAME = "duplicate-name"
    DUPLICATE_URL = "duplicate-url"
    UNKNOWN_ACCESS = "unknown-access"
    NOT_CHECKED = "not-checked"
    CREDENTIAL_NOT_ALLOWED = "credential-not-allowed"
    OUTSIDE_MOUNT = "outside-local-mount"
    DEPLOY_KEY_UNAVAILABLE = "deploy-key-unavailable"


class SourceRemovalRefusal(StrEnum):
    """Why a confirmed removal did not delete anything."""

    # The mirror or a built directory survived the attempt to take it away;
    # disk goes first, so no row is touched while either still stands.
    DISK_LEFTOVER = "disk-leftover"


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRemovalRefused:
    """A removal that deleted no row; `builds` is nothing where none was attempted."""

    reason: SourceRemovalRefusal
    mirror: PurePosixPath | None
    builds: tuple[PurePosixPath, ...] | None


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceRemovalStale:
    """Moved counts: nothing deleted, so no earlier yes covers more than it saw."""

    current: SourceRemoval


@dataclass(frozen=True, slots=True, kw_only=True)
class AddedSource:
    """A source that was just written, carrying the webhook secret shown once."""

    source: Source
    webhook_secret: str


@dataclass(frozen=True, slots=True, kw_only=True)
class NewSourceDraft:
    """What a person typed toward one new source, before it is checked or stored."""

    name: str
    url: str
    secret: str
    fingerprint: str
    # The draft key the Add form was shown; blank unless SSH was posted.
    key_draft_id: str = ""
    folder: str = ""


def hash_webhook_secret(secret: str) -> bytes:
    """The only form a webhook secret is stored in: SHA-256, never the value."""
    return sha256(secret.encode()).digest()


def _credential_of(posted: str, deploy_key: DeployKeyDraft | None) -> str:
    return posted.strip() if deploy_key is None else deploy_key.private_key


def _refusal_for(
    draft: NewSourceDraft,
    *,
    name: str,
    folder: str,
    url: str,
    existing: tuple[Source, ...],
) -> SourceRefusal | None:
    """The reason this draft cannot be stored, or nothing when it can.

    SSH checks with the account-owned draft key; every access kind needs that
    Check proof before it can create a source.
    """
    kind, secret = access_kind_of(url), draft.secret
    checks = (
        (
            name == RESERVED_SOURCE_NAME or _SOURCE_NAME.fullmatch(name) is None,
            SourceRefusal.MALFORMED_NAME,
        ),
        (not is_a_folder_path(folder), SourceRefusal.MALFORMED_FOLDER),
        (kind is None, SourceRefusal.UNKNOWN_ACCESS),
        (
            kind in (AccessKind.FILE, AccessKind.SSH) and bool(secret.strip()),
            SourceRefusal.CREDENTIAL_NOT_ALLOWED,
        ),
        (any(source.name == name for source in existing), SourceRefusal.DUPLICATE_NAME),
        (any(source.url == url for source in existing), SourceRefusal.DUPLICATE_URL),
    )
    return next((reason for matched, reason in checks if matched), None)


def _connection_fingerprint(
    key: bytes,
    *,
    owner_id: str,
    deploy_key: DeployKeyDraft | None,
    url: str,
    secret: str,
) -> str:
    """The proof Create later checks the posted values against.

    Signed rather than merely hashed: an unsigned digest of these same values is
    trivial for a client to forge without ever calling Check.
    """
    message = b"\0".join(
        (
            owner_id.encode(),
            b"" if deploy_key is None else deploy_key.id.encode(),
            b"" if deploy_key is None else deploy_key.public_key.encode(),
            url.encode(),
            _ADDED_REF.encode(),
            sha256(secret.encode()).digest(),
        ),
    )
    return hmac.new(key, message, sha256).hexdigest()


@dataclass(frozen=True, slots=True, kw_only=True)
class Decks:
    """Every use case the lobby has around the talks it knows."""

    sources: SourceStore
    key_drafts: DeployKeyDrafts
    folders: DeckFolders
    store: DeckStore
    builder: BuildRunner
    source_runs: SourceRuns
    checker: ConnectionChecker
    toolchain_themes: ToolchainThemes
    clock: Clock
    local_mount: LocalMount
    webhook_secrets: WebhookSecrets
    # How long a build may take before the refresh stops believing it is still
    # running: a process that died with the server leaves its attempt behind,
    # and nobody may read that as building for ever.
    build_bound: timedelta
    # Signs a Check-connection fingerprint, so a form cannot forge one for a
    # URL and secret it never actually proved reachable.
    fingerprint_key: bytes
    one_at_a_time: RLock = field(default_factory=RLock)
    builds: DeckBuilds = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Construct the build walk once, from the collaborators this instance holds."""
        object.__setattr__(
            self,
            "builds",
            DeckBuilds(
                store=self.store,
                builder=self.builder,
                clock=self.clock,
                build_bound=self.build_bound,
            ),
        )

    def refresh(self) -> None:
        """Take the sources in and build what changed, one refresh at a time.

        Whoever noticed the push calls this. A further request while one runs is
        already served by it, so a flood of them pulls once rather than once
        each, and the builds of one refresh follow one another instead of
        competing for the machine.
        """
        self._run_one_at_a_time(self._take_in_and_build, busy=None)

    def refresh_named(self, name: str) -> SourceFetch | None:
        """Take that one source in, build what changed and say what that was.

        A name nobody stored, or one removed before the lock is taken, answers
        None. A refresh already running holds the lock, so this call does
        nothing and answers a fetch without a run, which is not "nothing new".
        """
        if self._named(name) is None:
            return None
        return self._run_one_at_a_time(
            partial(self._take_in_and_build_named, name),
            busy=SourceFetch(run=None, decks_built=0, builds_failed=0, decks_removed=0),
        )

    def shown_source(self, name: str, *, viewer: str) -> SourcePage | None:
        """What that source's page says, its deck chips the viewer's own decks."""
        source = self._named(name)
        if source is None:
            return None
        listed = self._listed_source(source)
        return SourcePage(
            name=listed.name,
            url=listed.url,
            folder=source.folder,
            access=listed.access,
            state=listed.state,
            reason=listed.reason,
            fetched_at=listed.fetched_at,
            public_key=source.public_key,
            runs=tuple(shown_run(run) for run in self.source_runs.recent(source.id)),
            decks=tuple(
                SourceDeck(slug=deck.slug, title=deck.title)
                for deck in sorted(
                    (
                        deck
                        for deck in self.store.all()
                        if deck.source_id == source.id and deck.is_owned_by(viewer)
                    ),
                    key=lambda deck: deck.title,
                )
            ),
        )

    def renew_access(self, name: str, secret: str) -> bool:
        """Replace a source's HTTPS token (one printable line) or SSH deploy key.

        SSH needs no input; a local or unknown source has nothing to renew.
        """
        source = self._named(name)
        if source is None:
            return False
        kind = access_kind_of(source.url)
        if kind is AccessKind.SSH:
            return self.sources.renew_deploy_key(source.id)
        if kind is not AccessKind.HTTPS or not (
            secret.strip() and secret.isprintable()
        ):
            return False
        self.sources.put_credential(source.id, secret)
        return True

    def renew_webhook(self, name: str) -> str | None:
        """Mint a new webhook secret, store only its hash, and return the value once.

        A name nobody stored, or one removed before the write, has nothing to renew.
        """
        if self._named(name) is None:
            return None
        secret = self.webhook_secrets.new_secret()
        if not self.sources.put_hook_secret_hash(name, hash_webhook_secret(secret)):
            return None
        return secret

    def removal(self, name: str) -> SourceRemoval | None:
        """What removing that source would take with it, or nothing if unknown.

        Read-only: nothing changes until a person confirmed it (R1).
        """
        source = self._named(name)
        if source is None:
            return None
        return self._preview_of(source, self.store.for_source(source.id))

    def remove_source(
        self,
        name: str,
        *,
        expected_deck_count: int,
        expected_run_count: int,
    ) -> SourceRemoval | SourceRemovalRefused | SourceRemovalStale | None:
        """Delete that source, its decks, its runs, and everything they built.

        Waits for a refresh under way, so nothing of this source is still
        building when its directories go (R1). Name and counts are read inside
        the lock: a source removed meanwhile is unknown, and changed counts are
        handed back rather than deleted under a number nobody confirmed. Disk
        goes first: if the mirror or a run survives, or a build lies where no
        run can be placed, no row is deleted and the refusal names what stayed,
        so the source stays taken rather than a leftover becoming an address a
        later add reopens with a new secret (R3); a clean disk frees the URL (R2).
        """
        with self.one_at_a_time:
            source = self._named(name)
            if source is None:
                return None
            carried = self.store.for_source(source.id)
            preview = self._preview_of(source, carried)
            if (
                preview.deck_count != expected_deck_count
                or preview.run_count != expected_run_count
            ):
                return SourceRemovalStale(current=preview)
            left = SourceRemovalRefused(
                reason=SourceRemovalRefusal.DISK_LEFTOVER,
                mirror=self.folders.forget(source),
                builds=build_retention.builds_removed(self.builder, carried),
            )
            if left.mirror is not None or left.builds != ():
                return left
            self.store.remove_for_source(source.id)
            self.source_runs.remove_for_source(source.id)
            self.sources.remove(source.id)
        return preview

    def _preview_of(self, source: Source, carried: tuple[Deck, ...]) -> SourceRemoval:
        """What removing this source would take with it, over decks already read.

        `recent` is every run the source has, the table keeping no more per
        source. `carried` is every deck this source ever carried, marked removed
        or not: the set a removal deletes (`DeckStore.for_source`).
        """
        return SourceRemoval(
            name=source.name,
            deck_count=len(carried),
            run_count=len(self.source_runs.recent(source.id)),
        )

    def check_connection(
        self,
        *,
        url: str,
        secret: str,
        owner_id: str,
        key_draft_id: str,
    ) -> ConnectionCheckResult | SourceRefusal:
        """Probe the Add form's own URL and secret, writing nothing down.

        Every added source takes the one ref this instance follows; a check
        that answered a different ref would prove nothing Create relies on. A
        commit the probe found is shortened the way every other page shows
        one, so a reachable check and a stored source read alike.
        """
        address = url.strip()
        kind = access_kind_of(address)
        if kind is None:
            return SourceRefusal.UNKNOWN_ACCESS
        deploy_key = None
        if kind is AccessKind.SSH:
            deploy_key = self.key_drafts.unconsumed_for(
                owner_id,
                newer_than=self.clock.now() - DRAFT_MAX_AGE,
            )
            if deploy_key is None or deploy_key.id != key_draft_id:
                return ConnectionCheckResult(
                    failure=SourceRunFailure.REFUSED,
                    commit=None,
                    detail=None,
                )
        credential = _credential_of(secret, deploy_key)
        probed = self.checker.check(url=address, ref=_ADDED_REF, secret=credential)
        if probed.failure is not None:
            return probed.as_answered()
        return ConnectionCheckResult(
            failure=None,
            commit=None if probed.commit is None else probed.commit[:SHORT_COMMIT],
            detail=None,
            fingerprint=_connection_fingerprint(
                self.fingerprint_key,
                owner_id=owner_id,
                deploy_key=deploy_key,
                url=address,
                secret=credential,
            ),
        )

    def source_key_draft(self, *, owner_id: str) -> DeployKeyDraft:
        """This admin's own draft deploy key, reused while young or minted fresh.

        Called when the Add form opens, so the blessed board's public key is
        already there to copy before Create.
        """
        now = self.clock.now()
        return self.key_drafts.get_or_mint(
            owner_id,
            newer_than=now - DRAFT_MAX_AGE,
            at=now,
        )

    def add_source(
        self,
        draft: NewSourceDraft,
        *,
        owner_id: str,
    ) -> AddedSource | SourceRefusal:
        """Store a source with its secrets, fetch it once, return the webhook secret.

        The webhook secret is generated here and returned in the clear so the
        Add page's created view can show it once; only its hash is stored. The access
        secret is handed to the store and never returned. A source is stored
        only for the exact URL and secret a Check connection already proved
        reachable, never on the fingerprint of a different pair — SSH proves
        itself instead by binding a draft only this admin owns, minting a
        fresh one the moment a foreign or already-consumed id is offered. A
        file-kind address is resolved against the real mount before it is
        stored, so the row always carries the address git will actually open
        — a symlink or a `..` an operator's own spelling carried never
        reaches the row, and two spellings of the one real repository
        collide as the duplicate they are.
        """
        named = draft.name.strip()
        address = draft.url.strip()
        folder = normalized_folder(draft.folder)
        existing = self.sources.all()
        preflight = _refusal_for(
            draft,
            name=named,
            folder=folder,
            url=address,
            existing=existing,
        )
        if preflight is not None:
            return preflight
        ssh = access_kind_of(address) is AccessKind.SSH
        deploy_key = (
            self._deploy_key_for(owner_id=owner_id, draft_id=draft.key_draft_id)
            if ssh
            else None
        )
        if ssh and deploy_key is None:
            self.key_drafts.mint(owner_id, at=self.clock.now())
            return SourceRefusal.DEPLOY_KEY_UNAVAILABLE
        credential = _credential_of(draft.secret, deploy_key)
        proven = compare_digest(
            draft.fingerprint,
            _connection_fingerprint(
                self.fingerprint_key,
                owner_id=owner_id,
                deploy_key=deploy_key,
                url=address,
                secret=credential,
            ),
        )
        if not proven:
            return SourceRefusal.NOT_CHECKED
        canonical = self._canonical_address(address, existing)
        if isinstance(canonical, SourceRefusal):
            return canonical
        address = canonical
        webhook_secret = self.webhook_secrets.new_secret()
        write = SourceWrite(
            name=named,
            url=address,
            ref=_ADDED_REF,
            owner_id=owner_id,
            access_secret=credential,
            hook_secret_hash=hash_webhook_secret(webhook_secret),
            folder=folder,
            public_key=None if deploy_key is None else deploy_key.public_key,
        )
        stored = (
            self.sources.add_from_draft(write, draft_id=draft.key_draft_id)
            if deploy_key is not None
            else self.sources.add(write)
        )
        if stored is None:
            return SourceRefusal.DUPLICATE_NAME
        self.refresh_named(stored.name)
        return AddedSource(source=stored, webhook_secret=webhook_secret)

    def _deploy_key_for(
        self,
        *,
        owner_id: str,
        draft_id: str,
    ) -> DeployKeyDraft | None:
        """The current draft only when it belongs to this account and form."""
        draft = self.key_drafts.unconsumed_for(
            owner_id,
            newer_than=self.clock.now() - DRAFT_MAX_AGE,
        )
        return draft if draft is not None and draft.id == draft_id else None

    def _canonical_address(
        self,
        address: str,
        existing: tuple[Source, ...],
    ) -> str | SourceRefusal:
        """The local address the source row keeps, or its precise refusal."""
        if access_kind_of(address) is not AccessKind.FILE:
            return address
        canonical = self.local_mount.canonical_repository(address)
        if canonical is None:
            return SourceRefusal.OUTSIDE_MOUNT
        normalized = str(canonical)
        if any(source.url == normalized for source in existing):
            return SourceRefusal.DUPLICATE_URL
        return normalized

    def accept_hook(self, name: str, offered: str) -> bool:
        """Refresh that source when the offered secret matches the stored hash.

        A missing name and a wrong secret take the same compare against a dummy
        hash, so the time an answer takes does not say which names exist.
        """
        stored = self.sources.hook_secret_hash(name)
        matched = compare_digest(
            hash_webhook_secret(offered),
            stored if stored is not None else _NO_HASH,
        )
        if stored is None or not matched:
            return False
        self.refresh_named(name)
        return True

    def listed_sources(self) -> tuple[ListedSource, ...]:
        """Each source as the list shows it, reading the newest run only.

        No run is never-fetched and a successful newest run is reachable; a
        failed one carries its state, reason and instant, worded by
        `source_state_of`.
        """
        return tuple(self._listed_source(source) for source in self.sources.all())

    def listed(self, *, viewer: str) -> tuple[ListedDeck, ...]:
        """The viewer's own decks, newest changed first, reading no source.

        A page view costs a read, never a pull: what the source carries is the
        refresh's business.
        """
        newest_first = sorted(
            (deck for deck in self.store.all() if deck.is_owned_by(viewer)),
            key=lambda deck: (-deck.changed_at.timestamp(), deck.slug),
        )
        return tuple(
            ListedDeck(
                slug=deck.slug,
                title=deck.title,
                state=deck_state_of(deck),
                changed_at=deck.changed_at,
            )
            for deck in newest_first
        )

    def _owned(self, slug: str, viewer: str) -> Deck | None:
        """The deck, or nothing for an unknown slug and for another account's."""
        deck = self.store.get(slug)
        return deck if deck is not None and deck.is_owned_by(viewer) else None

    def page(self, slug: str, *, viewer: str) -> DeckPage | None:
        """What that deck's page says, or nothing while the viewer has no such deck.

        The commit named is the one the delivered talk was built from, not the
        one the source carries now: a person reads it to see whether their push
        is in what is on the screen (line 7), and while nothing is built there
        is nothing on the screen but what the source last carried.
        """
        deck = self._owned(slug, viewer)
        if deck is None:
            return None
        built = deck.build
        return DeckPage(
            slug=deck.slug,
            title=deck.title,
            source=self._address_of(deck),
            commit=(deck.commit if built is None else built.commit)[:SHORT_COMMIT],
            built_at=None if built is None else built.built_at,
            state=deck_state_of(deck),
            attempt=self._shown(deck.attempt),
            themes=self.toolchain_themes.names(),
        )

    def _shown(self, attempt: BuildAttempt | None) -> ShownAttempt | None:
        """What the page says about the build that ran last, while one has run.

        The commit is shortened the way the delivered one is: a person reads
        both off the same screen to see which push is which.
        """
        if attempt is None:
            return None
        return ShownAttempt(
            commit=attempt.commit[:SHORT_COMMIT],
            started_at=attempt.started_at,
            outcome=attempt.outcome,
            failure=attempt.failure,
        )

    def _address_of(self, deck: Deck) -> str | None:
        """The address of the source that carried this deck, never another's."""
        return next(
            (item.url for item in self.sources.all() if item.id == deck.source_id),
            None,
        )

    def built_talk(self, slug: str, *, viewer: str) -> Path | None:
        """The directory the viewer's deck's talk is delivered from, while built."""
        deck = self._owned(slug, viewer)
        if deck is None or deck.build is None:
            return None
        return deck.build.directory

    def exported_pdf(self, slug: str, *, viewer: str) -> Path | None:
        """The file the deck's PDF is handed over as, while one is exported.

        A slug that is not a folder's plain name names no deck here: the export
        is offered under that name, so a separator, a quote, or a line break in
        it would otherwise reach the answer's own header.
        """
        if not is_a_plain_folder_name(slug):
            return None
        deck = self._owned(slug, viewer)
        if deck is None or deck.build is None:
            return None
        return deck.build.pdf

    def open_build_file(self, written: Path) -> BinaryIO | None:
        """A file a build wrote, opened to be delivered, or nothing if it is not one."""
        return self.builder.read(written)

    def source_address(self, *, viewer: str) -> str | None:
        """The git address an empty list names, while the viewer has one source.

        With a second source of their own there is no single address the list
        could name, so it says only that it is empty.
        """
        sources = [source for source in self.sources.all() if source.owner_id == viewer]
        return sources[0].url if len(sources) == 1 else None

    def _listed_source(self, source: Source) -> ListedSource:
        """The row for that source, derived from its newest run and nothing else."""
        run = self.source_runs.newest(source.id)
        return ListedSource(
            name=source.name,
            url=source.url,
            access=access_kind_of(source.url),
            state=SourceState.NEVER_FETCHED if run is None else source_state_of(run),
            reason=None if run is None else run.reason,
            fetched_at=None if run is None else run.at,
        )

    def _run_one_at_a_time[Answer](
        self, work: Callable[[], Answer], *, busy: Answer
    ) -> Answer:
        """Run that work, or answer `busy` while another refresh holds the lock."""
        if not self.one_at_a_time.acquire(blocking=False):
            return busy
        try:
            answer = work()
            build_retention.sweep(self.builder, self.sources, self.store)
            return answer
        finally:
            self.one_at_a_time.release()

    def _named(self, name: str) -> Source | None:
        """The stored source that answers to this name, if this instance has one."""
        return next((item for item in self.sources.all() if item.name == name), None)

    def _take_in_and_build(self) -> None:
        """Sweep drafts nobody bound, then walk the sources one by one.

        A source is taken in and built before the next one is read, so a
        refresh costs its sources' pull bounds one after another instead of
        opening as many pulls at once as the instance has sources.
        """
        self.key_drafts.sweep(older_than=self.clock.now() - DRAFT_MAX_AGE)
        building = True
        for source in self.sources.all():
            building = self._take_in_and_count(source, building=building)[1]

    def _take_in_and_build_named(self, name: str) -> SourceFetch | None:
        """Take that source in, read again now that nobody can remove it."""
        source = self._named(name)
        return None if source is None else self._take_in_and_count(source)[0]

    def _take_in_and_count(
        self, source: Source, *, building: bool = True
    ) -> tuple[SourceFetch, bool]:
        """Take that source in, build it, count that, say if building goes on."""
        carried_before = self._visible_slugs(source)
        at = self.clock.now()
        poll = self.folders.folders(source)
        run = self._record_run(source, poll, at)
        if poll.folders is not None:
            self._take_in(source, poll.folders, at)
        walk = self.builds.build_what_changed(source, building=building)
        fetch = SourceFetch(
            run=shown_run(run),
            decks_built=sum(walk.delivered),
            builds_failed=len(walk.delivered) - sum(walk.delivered),
            decks_removed=len(carried_before - self._visible_slugs(source)),
        )
        return fetch, walk.still_building

    def _visible_slugs(self, source: Source) -> frozenset[str]:
        """The slugs of the decks that source carries and the lobby lists."""
        return frozenset(d.slug for d in self.store.all() if d.source_id == source.id)

    def _record_run(self, source: Source, poll: SourcePoll, at: datetime) -> SourceRun:
        """Record what the poll found or failed to as the source's newest run."""
        run = SourceRun(
            source_id=source.id,
            at=at,
            outcome=(
                SourceRunOutcome.FAILURE
                if poll.folders is None
                else SourceRunOutcome.SUCCESS
            ),
            commit=poll.commit,
            reason=poll.failure,
        )
        self.source_runs.record(run)
        return run

    def _take_in(
        self, source: Source, folders: tuple[DeckFolder, ...], at: datetime
    ) -> None:
        """Store every folder that carries both a manifest and slides.

        The folder name becomes the slug, so a changed title reaches no address.
        A folder whose name is not a folder's own name — such as `..`, planted as a git
        tree entry — is skipped here rather than stored, so it is never a path
        component. A folder the source has stopped carrying is marked as removed, and
        its deck deleted once the folder has been missing for `REMOVED_DECK_KEPT`; a
        folder that comes back under its old name before then loses that mark, so it is
        the deck it was. A folder whose name another source already carries is skipped
        too: the slug is one deck's address here, and the first source keeps it.
        """
        for folder in folders:
            if not is_a_plain_folder_name(folder.name):
                _log.warning(_NOT_A_FOLDERS_OWN_NAME, folder.name)
                continue
            if folder.title is None or SLIDES_FILE not in folder.file_names:
                continue
            taken_in = self.store.put(
                Deck(
                    slug=folder.name,
                    title=folder.title,
                    changed_at=folder.changed_at,
                    owner_id=source.owner_id,
                    source_id=source.id,
                    commit=folder.commit,
                    build=None,
                    attempt=None,
                ),
            )
            if not taken_in:
                _log.warning(_NAME_BELONGS_TO_ANOTHER_SOURCE, folder.name, source.name)
        present = frozenset(folder.name for folder in folders)
        build_retention.delete_absent(
            self.store, present=present, source_id=source.id, at=at
        )
