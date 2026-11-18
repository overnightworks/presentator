"""A source stored in an instance's database, as the poller reads it."""

from sqlalchemy import Engine

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.application.decks import hash_webhook_secret
from presentator.contracts.decks import SourceWrite
from tests.support.git_remote import GitRemote
from tests.support.lobby import INSTANCE_KEY
from tests.support.postgres import NO_FENCE


def sources_over(
    database: Engine, *, instance_key: str = INSTANCE_KEY
) -> PostgresSourceStore:
    """The real source store over the instance's database, keyed as it says.

    What the instance itself writes is keyed by its own instance key; another
    key stands for a row this installation did not write.
    """
    return PostgresSourceStore(
        engine=database,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(instance_key),
        fence=NO_FENCE,
    )


def add_a_polled_source(  # noqa: PLR0913 - each part is one thing a test names
    database: Engine,
    remote: GitRemote,
    *,
    name: str,
    owner_id: str,
    access_secret: str,
    hook_secret: str,
) -> None:
    """Store that remote as a source its owner added, armed with a hook secret."""
    added = sources_over(database).add(
        SourceWrite(
            name=name,
            url=remote.url,
            ref="main",
            owner_id=owner_id,
            access_secret=access_secret,
            hook_secret_hash=hash_webhook_secret(hook_secret),
        ),
    )
    assert added is not None
