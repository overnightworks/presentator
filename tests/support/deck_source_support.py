"""Deck folders, mirrors and decks the adapter and API tests build over a source."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from gitmirror.model import CredentialResolver
from presentator.adapters.decks import (
    FilesystemLocalMount,
    MirroredDeckFolders,
    SourceMirrors,
)
from presentator.contracts.decks import Build, Deck, Source
from presentator.contracts.models import Role, User
from tests.presentator.adapters.support.credentials_support import OpenCredentials
from tests.support.git_remote import MAIN_BRANCH

PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
OWNER = User(id="the-admin", username="felix", role=Role.ADMIN)
SOURCE_ID = "the-source-that-carried-it"
SOURCE_NAME = "decks"
A_GENEROUS_BOUND = timedelta(seconds=30)
BUILT_AT = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)
COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


def a_source(url: str) -> Source:
    """A source whose secret, if it had one, no test of this file resolves."""
    return Source(
        id=SOURCE_ID,
        name=SOURCE_NAME,
        url=url,
        ref=MAIN_BRANCH,
        owner_id=OWNER.id,
    )


def folders_under(
    tmp_path: Path,
    *,
    pull_timeout: timedelta = A_GENEROUS_BOUND,
    credentials: CredentialResolver | None = None,
    local_mount: FilesystemLocalMount | None = None,
) -> MirroredDeckFolders:
    return MirroredDeckFolders(
        mirrors=mirrors_under(
            tmp_path,
            timeout=pull_timeout,
            credentials=credentials,
            local_mount=local_mount,
        ),
    )


def mirrors_under(
    tmp_path: Path,
    *,
    timeout: timedelta = A_GENEROUS_BOUND,
    credentials: CredentialResolver | None = None,
    local_mount: FilesystemLocalMount | None = None,
) -> SourceMirrors:
    return SourceMirrors(
        directory=tmp_path / "mirrors",
        credentials=OpenCredentials() if credentials is None else credentials,
        pull_timeout=timeout,
        local_mount=(
            FilesystemLocalMount(mount=tmp_path) if local_mount is None else local_mount
        ),
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
        directory=tmp_path / "builds" / "kundenfeedback" / commit / "talk",
        pdf=tmp_path / "builds" / "kundenfeedback" / commit / "deck.pdf",
        commit=commit,
        built_at=BUILT_AT,
    )
