"""The pull resolves a source's secret from the source's own Postgres row."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine

from gitmirror.model import CredentialReference
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.sources import (
    PostgresSourceCredentials,
    PostgresSourceStore,
)
from presentator.adapters.secrets import secret_box
from tests.presentator.adapters.support.source_stores_support import (
    ANOTHER_INSTANCE_KEY,
    INSTANCE_KEY,
    seed_postgres_owner,
)
from tests.presentator.adapters.support.source_writer_process import a_source_write
from tests.support.postgres import NO_FENCE, engine_on

_OWNER = "the-owner"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


def _credentials(engine: Engine, instance_key: str) -> PostgresSourceCredentials:
    return PostgresSourceCredentials(engine=engine, box=secret_box(instance_key))


def _a_stored_source(engine: Engine) -> str:
    seed_postgres_owner(engine, _OWNER)
    source = PostgresSourceStore(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
        fence=NO_FENCE,
    ).add(a_source_write("kept", "https://git.example.test/kept.git", _OWNER))
    assert source is not None
    return source.id


def test_a_stored_source_resolves_to_its_secret(engine: Engine) -> None:
    source_id = _a_stored_source(engine)

    resolved = _credentials(engine, INSTANCE_KEY).resolve(
        CredentialReference(name=source_id)
    )

    assert resolved == "token of kept"


def test_a_source_this_instance_does_not_carry_resolves_to_nothing(
    engine: Engine,
) -> None:
    resolved = _credentials(engine, INSTANCE_KEY).resolve(
        CredentialReference(name="never-stored")
    )

    assert resolved is None


def test_ciphertext_another_instance_key_wrote_resolves_to_nothing(
    engine: Engine,
) -> None:
    source_id = _a_stored_source(engine)

    resolved = _credentials(engine, ANOTHER_INSTANCE_KEY).resolve(
        CredentialReference(name=source_id)
    )

    assert resolved is None
