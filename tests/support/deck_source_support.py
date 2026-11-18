"""Deck folders, mirrors and decks the adapter and API tests build over a source."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, text

from gitmirror.model import CredentialResolver
from presentator.adapters.decks import MirroredDeckFolders, SourceMirrors
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.decks import Build, BuildRun, Deck, Source, SourceWrite
from presentator.contracts.models import Role, User
from tests.presentator.adapters.support.credentials_support import OpenCredentials
from tests.presentator.adapters.support.key_draft_support import INSTANCE_KEY
from tests.support.git_remote import MAIN_BRANCH
from tests.support.mirror_support import PinsInMemory
from tests.support.people import seed_user
from tests.support.postgres import NO_FENCE

PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
OWNER = User(id="the-admin", username="felix", role=Role.ADMIN)
SOURCE_ID = "the-source-that-carried-it"
SOURCE_NAME = "decks"
A_GENEROUS_BOUND = timedelta(seconds=30)
BUILT_AT = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


def a_source(url: str, folder: str = "") -> Source:
    """A source whose secret, if it had one, no test that uses it resolves."""
    return Source(
        id=SOURCE_ID,
        name=SOURCE_NAME,
        url=url,
        ref=MAIN_BRANCH,
        owner_id=OWNER.id,
        folder=folder,
    )


def a_source_database(engine: Engine) -> Engine:
    """The database, holding the admin who owns sources."""
    seed_user(engine, OWNER)
    return engine


_SEED_SOURCE = text(
    "INSERT INTO sources (id, name, url, ref, encrypted_secret, owner_id)"
    " VALUES (:id, :id, :url, 'main', '', :owner_id)"
)


def seed_source_rows(engine: Engine, *source_ids: str) -> Engine:
    """The admin and a bare source row under each id, for decks to point at."""
    a_source_database(engine)
    with engine.begin() as connection:
        for source_id in source_ids:
            connection.execute(
                _SEED_SOURCE,
                {
                    "id": source_id,
                    "url": f"git@example.invalid:{source_id}.git",
                    "owner_id": OWNER.id,
                },
            )
    return engine


def a_source_store(engine: Engine) -> PostgresSourceStore:
    return PostgresSourceStore(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
        fence=NO_FENCE,
    )


def a_source_write(
    url: str, *, name: str = SOURCE_NAME, folder: str = ""
) -> SourceWrite:
    return SourceWrite(
        name=name,
        url=url,
        ref=MAIN_BRANCH,
        owner_id=OWNER.id,
        access_secret="",
        hook_secret_hash=b"\x11" * 32,
        folder=folder,
    )


def folders_under(
    tmp_path: Path,
    *,
    pull_timeout: timedelta = A_GENEROUS_BOUND,
    credentials: CredentialResolver | None = None,
) -> MirroredDeckFolders:
    return MirroredDeckFolders(
        mirrors=mirrors_under(
            tmp_path,
            timeout=pull_timeout,
            credentials=credentials,
        ),
    )


def mirrors_under(
    tmp_path: Path,
    *,
    timeout: timedelta = A_GENEROUS_BOUND,
    credentials: CredentialResolver | None = None,
) -> SourceMirrors:
    return SourceMirrors(
        directory=tmp_path / "mirrors",
        credentials=OpenCredentials() if credentials is None else credentials,
        pull_timeout=timeout,
        host_keys=PinsInMemory(),
    )


def a_deck(
    slug: str = "kundenfeedback",
    *,
    title: str = "Kundenfeedback",
    commit: str = COMMIT,
    source_id: str = SOURCE_ID,
) -> Deck:
    return Deck(
        slug=slug,
        title=title,
        changed_at=PUSHED_AT,
        owner_id=OWNER.id,
        source_id=source_id,
        commit=commit,
        build=None,
        attempt=None,
    )


def a_build(tmp_path: Path, *, commit: str = COMMIT) -> Build:
    return Build(
        run=BuildRun(commit),
        directory=tmp_path / "builds" / commit / "out" / "talk",
        pdf=tmp_path / "builds" / commit / "out" / "deck.pdf",
        commit=commit,
        built_at=BUILT_AT,
        toolchain=None,
    )
