"""The Postgres stores over a database of the test's own, with numbered ids and keys."""

import itertools
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url
from webauth.ports import AccountKind

from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import DeployKeyPair, secret_box
from presentator.contracts.models import Role
from presentator.ports.decks import DeckStore, DeployKeyDrafts, SourceRuns, SourceStore
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)
from tests.support.postgres import NO_FENCE, engine_on

INSTANCE_KEY: Final = "an instance key of thirty-two ch"
ANOTHER_INSTANCE_KEY: Final = "another instance key of thirty-2"
# A zone far from UTC, so a store that reads an instant in the session's zone
# shows it.
_SESSION_TIME_ZONE: Final = "Pacific/Auckland"


class NumberedIdentifiers:
    """Identifiers that count up, so a test can name what a write is given."""

    def __init__(self) -> None:
        """Count from one."""
        self._numbers = itertools.count(1)

    def new_id(self) -> str:
        return f"id-{next(self._numbers)}"


@dataclass(frozen=True, slots=True)
class Adapters:
    """The stores over a database of their own."""

    drafts: DeployKeyDrafts
    sources: SourceStore
    runs: SourceRuns
    decks: DeckStore
    settings: InstanceSettingsStore
    preferences: PersonPreferencesStore
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


@contextmanager
def postgres_adapters(
    database_url: str, key_pairs: NumberedKeyPairs
) -> Generator[Adapters]:
    """The stores on a database whose sessions read timestamps in a far zone."""
    _read_timestamps_in_a_far_zone(database_url)
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
            fence=NO_FENCE,
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
            runs=PostgresSourceRunStore(engine=engine, fence=NO_FENCE),
            decks=PostgresDeckStore(engine=engine, fence=NO_FENCE),
            settings=PostgresInstanceSettingsStore(engine),
            preferences=PostgresPersonPreferencesStore(engine),
            seed_owner=lambda owner_id: seed_postgres_owner(engine, owner_id),
            drafts_opened_with=drafts_opened_with,
            sources_opened_with=sources_opened_with,
            secret_of=secret_of,
        )
    finally:
        engine.dispose()


def _read_timestamps_in_a_far_zone(database_url: str) -> None:
    engine = engine_on(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f'ALTER DATABASE "{make_url(database_url).database}"'
                    f" SET timezone TO '{_SESSION_TIME_ZONE}'"
                )
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
