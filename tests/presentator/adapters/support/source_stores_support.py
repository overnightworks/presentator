"""The source stores over SQLite or Postgres, for tests that compare the two."""

import itertools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from sqlalchemy import Engine, text
from webauth.ports import AccountKind

from presentator.adapters.decks import (
    SqliteSourceRunStore,
    SqliteSourceStore,
    create_deck_tables,
)
from presentator.adapters.identity import create_identity_tables
from presentator.adapters.key_drafts import SqliteSourceKeyDrafts
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import DeployKeyPair, secret_box
from presentator.adapters.sqlite import rows
from presentator.contracts.models import Role
from presentator.ports.decks import DeployKeyDrafts, SourceRuns, SourceStore
from tests.support.postgres import engine_on

INSTANCE_KEY: Final = "an instance key of thirty-two ch"
ANOTHER_INSTANCE_KEY: Final = "another instance key of thirty-2"


class NumberedIdentifiers:
    """Identifiers that count up, so two adapters given the same writes agree."""

    def __init__(self) -> None:
        """Count from one."""
        self._numbers = itertools.count(1)

    def new_id(self) -> str:
        return f"id-{next(self._numbers)}"


@dataclass(frozen=True, slots=True)
class Adapters:
    """One adapter's stores over a database of its own."""

    drafts: DeployKeyDrafts
    sources: SourceStore
    runs: SourceRuns
    seed_owner: Callable[[str], None]
    drafts_opened_with: Callable[[str], DeployKeyDrafts]
    sources_opened_with: Callable[[str], SourceStore]
    # The secret a source's row holds, opened with the instance key.
    secret_of: Callable[[str], str | None]


class NumberedKeyPairs:
    """Stands in for key generation: the next pair is the next number."""

    def __init__(self) -> None:
        """Count from one."""
        self._numbers = itertools.count(1)

    def restart(self) -> None:
        self._numbers = itertools.count(1)

    def __call__(self) -> DeployKeyPair:
        number = next(self._numbers)
        return DeployKeyPair(
            private_key=f"private key {number}", public_key=f"public key {number}"
        )


def sqlite_adapters(directory: Path, key_pairs: NumberedKeyPairs) -> Adapters:
    database = directory / "presentator.sqlite3"
    create_identity_tables(database)
    create_deck_tables(database)

    def seed_owner(owner_id: str) -> None:
        with rows(database) as cursor:
            cursor.execute(
                "INSERT INTO users (id, username, role, kind) VALUES (?, ?, ?, ?)",
                (owner_id, owner_id, Role.ADMIN.value, AccountKind.PERSON.value),
            )

    identifiers = NumberedIdentifiers()

    def drafts_opened_with(instance_key: str) -> DeployKeyDrafts:
        return SqliteSourceKeyDrafts(
            database=database,
            identifiers=identifiers,
            box=secret_box(instance_key),
            new_key_pair=key_pairs,
        )

    def sources_opened_with(instance_key: str) -> SourceStore:
        return SqliteSourceStore(
            database=database,
            identifiers=identifiers,
            box=secret_box(instance_key),
            new_key_pair=key_pairs,
        )

    def secret_of(source_id: str) -> str | None:
        with rows(database) as cursor:
            found = cursor.execute(
                "SELECT encrypted_secret FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        return None if found is None else secret_box(INSTANCE_KEY).decrypt(found[0])

    return Adapters(
        drafts=drafts_opened_with(INSTANCE_KEY),
        sources=sources_opened_with(INSTANCE_KEY),
        runs=SqliteSourceRunStore(database=database),
        seed_owner=seed_owner,
        drafts_opened_with=drafts_opened_with,
        sources_opened_with=sources_opened_with,
        secret_of=secret_of,
    )


@contextmanager
def postgres_adapters(
    database_url: str, key_pairs: NumberedKeyPairs
) -> Generator[Adapters]:
    engine = engine_on(database_url)
    identifiers = NumberedIdentifiers()

    def drafts_opened_with(instance_key: str) -> DeployKeyDrafts:
        return PostgresSourceKeyDrafts(
            engine=engine,
            identifiers=identifiers,
            box=secret_box(instance_key),
            new_key_pair=key_pairs,
        )

    def sources_opened_with(instance_key: str) -> SourceStore:
        return PostgresSourceStore(
            engine=engine,
            identifiers=identifiers,
            box=secret_box(instance_key),
            new_key_pair=key_pairs,
        )

    def secret_of(source_id: str) -> str | None:
        with engine.connect() as connection:
            found = connection.execute(
                text("SELECT encrypted_secret FROM sources WHERE id = :source_id"),
                {"source_id": source_id},
            ).fetchone()
        return None if found is None else secret_box(INSTANCE_KEY).decrypt(found[0])

    try:
        yield Adapters(
            drafts=drafts_opened_with(INSTANCE_KEY),
            sources=sources_opened_with(INSTANCE_KEY),
            runs=PostgresSourceRunStore(engine=engine),
            seed_owner=lambda owner_id: seed_postgres_owner(engine, owner_id),
            drafts_opened_with=drafts_opened_with,
            sources_opened_with=sources_opened_with,
            secret_of=secret_of,
        )
    finally:
        engine.dispose()


def seed_postgres_owner(engine: Engine, owner_id: str) -> None:
    """An admin account the source tables' foreign keys can point at."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, username, role, kind)"
                " VALUES (:owner_id, :owner_id, :role, :kind)"
            ),
            {
                "owner_id": owner_id,
                "role": Role.ADMIN.value,
                "kind": AccountKind.PERSON.value,
            },
        )
