"""The `source_key_drafts` store over a test database, shared by its test modules."""

from typing import Final

from sqlalchemy import Engine

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.secrets import secret_box

INSTANCE_KEY: Final = "an instance key of thirty-two ch"


def a_key_drafts_store(
    engine: Engine,
    *,
    instance_key: str = INSTANCE_KEY,
) -> PostgresSourceKeyDrafts:
    """The `source_key_drafts` table over that database."""
    return PostgresSourceKeyDrafts(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(instance_key),
    )
