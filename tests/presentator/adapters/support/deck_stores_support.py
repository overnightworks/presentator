"""The deck, settings and preference stores over SQLite or Postgres, side by side."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.adapters.preferences import (
    SqliteInstanceSettingsStore,
    SqlitePersonPreferencesStore,
    create_preference_tables,
)
from presentator.ports.decks import DeckStore
from presentator.ports.preferences import (
    InstanceSettingsStore,
    PersonPreferencesStore,
)
from tests.presentator.adapters.support.source_stores_support import (
    Adapters,
    NumberedKeyPairs,
    postgres_adapters,
    sqlite_adapters,
)
from tests.support.postgres import engine_on

# A zone far from UTC, so a store that reads an instant in the session's zone
# shows it.
_SESSION_TIME_ZONE = "Pacific/Auckland"


@dataclass(frozen=True, slots=True)
class DeckAdapters:
    """One adapter's deck, settings and preference stores beside its source stores."""

    sources: Adapters
    decks: DeckStore
    settings: InstanceSettingsStore
    preferences: PersonPreferencesStore


def sqlite_deck_adapters(directory: Path, key_pairs: NumberedKeyPairs) -> DeckAdapters:
    sources = sqlite_adapters(directory, key_pairs)
    database = directory / "presentator.sqlite3"
    create_preference_tables(database)
    return DeckAdapters(
        sources=sources,
        decks=SqliteDeckStore(database=database),
        settings=SqliteInstanceSettingsStore(database),
        preferences=SqlitePersonPreferencesStore(database),
    )


@contextmanager
def postgres_deck_adapters(
    database_url: str, key_pairs: NumberedKeyPairs
) -> Generator[DeckAdapters]:
    """The stores on a database whose sessions read timestamps in a far zone."""
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
    engine = engine_on(database_url)
    try:
        with postgres_adapters(database_url, key_pairs) as sources:
            yield DeckAdapters(
                sources=sources,
                decks=PostgresDeckStore(engine=engine),
                settings=PostgresInstanceSettingsStore(engine),
                preferences=PostgresPersonPreferencesStore(engine),
            )
    finally:
        engine.dispose()
