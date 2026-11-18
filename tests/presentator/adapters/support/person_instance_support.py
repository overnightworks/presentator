"""Two people's rows through the stores, beside real mirror and build directories."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr
from webauth.ports import SessionRecordStore

from presentator.adapters.build_cache import BuildCache
from presentator.adapters.builds import HostToolchain, SlidevBuilds
from presentator.adapters.decks import MirroredDeckFolders, SourceMirrors
from presentator.adapters.secrets import TokenWebhookSecrets
from presentator.application.decks import Decks
from presentator.application.people import People
from presentator.contracts.decks import (
    Build,
    BuildRun,
    Deck,
    Source,
    SourceRun,
    SourceRunOutcome,
    SourceWrite,
    run_name_holding,
)
from presentator.contracts.models import Role, User
from presentator.contracts.preferences import PersonPreferences, ThemeChoice
from presentator.ports.decks import DeckStore, DeployKeyDrafts, SourceRuns, SourceStore
from presentator.ports.identity import PersonAccounts, PersonSessions
from presentator.ports.preferences import PersonPreferencesStore
from tests.presentator.adapters.support.credentials_support import OpenCredentials
from tests.presentator.application.support.fakes import (
    THE_TOOLCHAIN,
    FakeConnectionChecker,
    FakeRefreshOwnership,
    FakeToolchainThemes,
    FrozenClock,
)
from tests.support.people import a_session_id
from tests.support.run_objects import A_CACHE_BOUND, DirectoryRunObjects

_NOW = datetime(2026, 1, 15, 9, tzinfo=UTC)
THE_PERSON = User(id="the-person", username="pia", role=Role.ADMIN)
ANOTHER = User(id="another-person", username="ben", role=Role.USER)
_A_BOUND = timedelta(seconds=30)
WHAT_THE_GIT_HOST_EXPECTS = "the read-only words only this test made up"
_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


@dataclass
class Directories:
    """The directories and use cases around a database holding two people's rows."""

    mirrors: SourceMirrors
    builds: Path
    decks: Decks
    people: People
    kept_by: dict[str, tuple[Source, Path, Path]]
    store: DirectoryRunObjects

    def mirror_of(self, owner: User) -> Path:
        return self.kept_by[owner.id][1]

    def build_of(self, owner: User) -> Path:
        return self.kept_by[owner.id][2]

    def stored(self, owner: User) -> bool:
        """Whether the store still holds the run of that person's build."""
        names = self.store.names()
        assert names is not None
        return run_name_holding(self.build_of(owner)) in names


class SessionStore(SessionRecordStore, PersonSessions, Protocol):
    """A session store as the seeding writes it and the erasure reads it."""


@dataclass(frozen=True)
class Stores:
    """Every store two people's rows are written through and erased from."""

    seed_user: Callable[[User], None]
    sources: SourceStore
    decks: DeckStore
    runs: SourceRuns
    key_drafts: DeployKeyDrafts
    accounts: PersonAccounts
    sessions: SessionStore
    preferences: PersonPreferencesStore


def _seed_person(
    person: User,
    stores: Stores,
    mirrors: SourceMirrors,
    builds: Path,
) -> tuple[Source, Path, Path]:
    stores.seed_user(person)
    stores.sessions.create(
        person.id,
        session_id=a_session_id(),
        now=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        refresh_token=SecretStr("a-refresh-token"),
        id_token=SecretStr("an-id-token"),
        account_language=None,
    )
    stores.preferences.write(person.id, PersonPreferences(theme=ThemeChoice.DARK))
    stores.key_drafts.mint(person.id, at=_NOW)
    source = stores.sources.add(
        SourceWrite(
            name=f"{person.username}-decks",
            url=f"git@example.invalid:{person.username}.git",
            ref="main",
            owner_id=person.id,
            access_secret=WHAT_THE_GIT_HOST_EXPECTS,
            hook_secret_hash=b"\x11" * 32,
            public_key=None,
        ),
    )
    assert source is not None
    mirror_directory = mirrors.of(source)
    assert mirror_directory is not None
    mirror = mirror_directory.directory
    mirror.mkdir(parents=True)
    (mirror / "HEAD").write_text("ref: refs/heads/main")
    build = builds / f"{person.username}-run" / "out" / "talk"
    build.mkdir(parents=True)
    (build / "index.html").write_text("a talk")
    (build.parent / "deck.pdf").write_bytes(b"%PDF-1.7")
    _seed_deck(stores, person, source, build)
    return source, mirror, build


def _seed_deck(stores: Stores, person: User, source: Source, build: Path) -> None:
    slug = f"{person.username}-talk"
    assert stores.decks.put(
        Deck(
            slug=slug,
            title="A talk",
            changed_at=_NOW,
            owner_id=person.id,
            source_id=source.id,
            commit=_COMMIT,
            build=None,
            attempt=None,
        ),
    )
    stores.decks.put_build(
        slug,
        Build(
            run=BuildRun(run_name_holding(build)),
            directory=build,
            pdf=build.parent / "deck.pdf",
            commit=_COMMIT,
            built_at=_NOW,
            toolchain=None,
        ),
    )
    stores.runs.record(
        SourceRun(
            source_id=source.id,
            at=_NOW,
            outcome=SourceRunOutcome.SUCCESS,
            commit=_COMMIT,
            reason=None,
        ),
    )


def _decks_application(
    stores: Stores,
    mirrors: SourceMirrors,
    cache: BuildCache,
    tmp_path: Path,
) -> Decks:
    return Decks(
        sources=stores.sources,
        key_drafts=stores.key_drafts,
        folders=MirroredDeckFolders(mirrors=mirrors),
        store=stores.decks,
        builder=SlidevBuilds(
            cache=cache,
            identity=THE_TOOLCHAIN,
            toolchain=HostToolchain(project=tmp_path, bound=_A_BOUND),
            mirrors=mirrors,
            output_megabytes=1,
        ),
        source_runs=stores.runs,
        checker=FakeConnectionChecker(),
        toolchain_themes=FakeToolchainThemes(),
        clock=FrozenClock(instant=_NOW),
        ownership=FakeRefreshOwnership(),
        poll_interval=timedelta(0),
        fingerprint_key=b"a fingerprint key of some length",
        webhook_secrets=TokenWebhookSecrets(),
    )


def directories_with_both_people(stores: Stores, tmp_path: Path) -> Directories:
    """Each person has an account, a session, preferences, a draft and a source.

    The source carries a deck with a build, a run, a mirror and a build
    directory on disk, kept in the store.
    """
    mirrors = SourceMirrors(
        directory=tmp_path / "mirrors",
        credentials=OpenCredentials(),
        pull_timeout=_A_BOUND,
    )
    builds = tmp_path / "builds"
    kept_by = {
        person.id: _seed_person(person, stores, mirrors, builds)
        for person in (THE_PERSON, ANOTHER)
    }
    store = DirectoryRunObjects(tmp_path / "store")
    cache = BuildCache(root=builds, store=store, bound_bytes=A_CACHE_BOUND)
    for _, _, build in kept_by.values():
        assert cache.keep(run_name_holding(build))
    decks = _decks_application(stores, mirrors, cache, tmp_path)
    return Directories(
        mirrors=mirrors,
        builds=builds,
        decks=decks,
        people=People(
            decks=decks,
            accounts=stores.accounts,
            sessions=stores.sessions,
            preferences=stores.preferences,
            key_drafts=stores.key_drafts,
        ),
        kept_by=kept_by,
        store=store,
    )
