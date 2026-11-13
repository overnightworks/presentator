"""A source stored in an instance's database, as the poller reads it."""

from pathlib import Path

from presentator.adapters.decks import SqliteSourceStore
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.secrets import secret_box
from presentator.application.decks import hash_webhook_secret
from presentator.contracts.decks import SourceWrite
from tests.support.git_remote import GitRemote
from tests.support.lobby import INSTANCE_KEY


def sources_over(
    database: Path, *, instance_key: str = INSTANCE_KEY
) -> SqliteSourceStore:
    """The real source store over the instance's file, keyed as it says.

    What the instance itself writes is keyed by its own instance key; another
    key stands for a row this installation did not write.
    """
    return SqliteSourceStore(
        database=database,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(instance_key),
    )


def add_a_polled_source(  # noqa: PLR0913 - each part is one thing a test names
    database: Path,
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
