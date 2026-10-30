"""The deck scenario builders: a source, its folders and Decks over fakes."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from presentator.application.decks import Decks
from presentator.contracts.decks import MANIFEST_FILE, SLIDES_FILE, DeckFolder, Source
from presentator.ports.decks import DeckFolders
from tests.presentator.application.support.fakes import (
    FakeBuildRunner,
    FakeConnectionChecker,
    FakeDeckFolders,
    FakeDeckStore,
    FakeDeployKeyDrafts,
    FakeLocalMount,
    FakeSourceRunStore,
    FakeSourceStore,
    FakeToolchainThemes,
    FakeWebhookSecrets,
    FrozenClock,
)

NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)
OWNER = "the account that set the instance up"


def a_source(name: str, *, identifier: str) -> Source:
    return Source(
        id=identifier,
        name=name,
        url=f"git@example.invalid:{name}.git",
        ref="main",
        owner_id=OWNER,
    )


SOURCE = a_source("decks", identifier="the-configured-source")
A_DECK = frozenset({MANIFEST_FILE, SLIDES_FILE})
COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
# Long enough that no test's build reaches it by accident, so an attempt is
# only ever given up on where a test says so.
BUILD_BOUND = timedelta(minutes=5)
FINGERPRINT_KEY = b"what only this test's instance signs a fingerprint with"


def a_folder(
    name: str,
    *,
    title: str | None = "A talk",
    file_names: frozenset[str] = A_DECK,
    changed_ago: timedelta = timedelta(minutes=2),
    commit: str = COMMIT,
) -> DeckFolder:
    return DeckFolder(
        name=name,
        file_names=file_names,
        title=title,
        changed_at=NOW - changed_ago,
        commit=commit,
    )


def carrying(*folders: DeckFolder, source: Source = SOURCE) -> FakeDeckFolders:
    """A mirror in which that source carries exactly those folders."""
    return FakeDeckFolders(carried={source.id: folders})


def having(*sources: Source) -> FakeSourceStore:
    """The sources an instance already has."""
    return FakeSourceStore(sources=list(sources))


@dataclass(frozen=True, slots=True, kw_only=True)
class DecksFakes:
    """The fakes a `decks_over` scenario runs against, besides source and mirror.

    Grouped into one value so `decks_over` keeps four parameters; a test names
    only the fake it cares about, and every field it leaves unnamed gets its
    own fresh instance rather than one every other test would share.
    """

    store: FakeDeckStore = field(default_factory=FakeDeckStore)
    builder: FakeBuildRunner = field(default_factory=FakeBuildRunner)
    source_runs: FakeSourceRunStore = field(default_factory=FakeSourceRunStore)
    checker: FakeConnectionChecker = field(default_factory=FakeConnectionChecker)
    toolchain_themes: FakeToolchainThemes = field(default_factory=FakeToolchainThemes)
    clock: FrozenClock = field(default_factory=lambda: FrozenClock(instant=NOW))
    local_mount: FakeLocalMount = field(default_factory=FakeLocalMount)
    key_drafts: FakeDeployKeyDrafts = field(default_factory=FakeDeployKeyDrafts)
    webhook_secrets: FakeWebhookSecrets = field(default_factory=FakeWebhookSecrets)


def decks_over(
    *folders: DeckFolder,
    sources: FakeSourceStore | None = None,
    mirror: DeckFolders | None = None,
    fakes: DecksFakes | None = None,
) -> Decks:
    resolved = DecksFakes() if fakes is None else fakes
    source_store = having(SOURCE) if sources is None else sources
    source_store.key_drafts = resolved.key_drafts
    return Decks(
        sources=source_store,
        key_drafts=resolved.key_drafts,
        folders=carrying(*folders) if mirror is None else mirror,
        store=resolved.store,
        builder=resolved.builder,
        source_runs=resolved.source_runs,
        checker=resolved.checker,
        toolchain_themes=resolved.toolchain_themes,
        build_bound=BUILD_BOUND,
        fingerprint_key=FINGERPRINT_KEY,
        clock=resolved.clock,
        local_mount=resolved.local_mount,
        webhook_secrets=resolved.webhook_secrets,
    )
