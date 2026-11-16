"""The migrations of the database, and the rule for serving what they made."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import SQLAlchemyError

from presentator.adapters.postgres.locks import MIGRATION_LOCK, AdvisoryLock, parameters

MIGRATIONS_DIRECTORY: Final = Path(__file__).parent / "migrations"
_TAKE_LOCK: Final = text("SELECT pg_advisory_lock(:lock_class, :instance)")
_RELEASE_LOCK: Final = text("SELECT pg_advisory_unlock(:lock_class, :instance)")
_log = logging.getLogger(__name__)


class RefusalReason(StrEnum):
    """Why a database is not served or migrated: the closed set of `reason` codes."""

    NOT_ANSWERING = "not_answering"
    NO_REVISION = "no_revision"
    BEHIND_THIS_RELEASE = "behind_this_release"
    NEWER_THAN_THIS_RELEASE = "newer_than_this_release"


class DatabaseRefusedError(Exception):
    """The database is not served or migrated, for `reason`.

    It carries the code and never what the driver says, which may quote the
    address the connection used.
    """

    def __init__(self, reason: RefusalReason) -> None:
        """Refuse the database for `reason`."""
        super().__init__(reason)
        self.reason = reason


def cap_alembic_logging() -> None:
    """Keep alembic's own progress text out of the JSON log; it is free text there."""
    logging.getLogger("alembic").setLevel(logging.WARNING)


def migrate(engine: Engine) -> None:
    """Bring the database to head; concurrent runs wait for one another.

    Migrations only add, so a database whose revision this release does not know
    was migrated by a newer release: it stays as it is, which lets a rollback of
    the release run its migration step without harm. A database that does not
    answer is refused.
    """
    config = _config(MIGRATIONS_DIRECTORY)
    history = ScriptDirectory.from_config(config)
    try:
        connection = engine.connect()
    except SQLAlchemyError:
        raise DatabaseRefusedError(RefusalReason.NOT_ANSWERING) from None
    with connection:
        _hold(connection, MIGRATION_LOCK)
        try:
            if _is_from_a_newer_release(connection, history):
                _log.info(
                    "migration.refused",
                    extra={"reason": RefusalReason.NEWER_THAN_THIS_RELEASE},
                )
                return
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            connection.rollback()
            _release(connection, MIGRATION_LOCK)


def migration_history(directory: Path = MIGRATIONS_DIRECTORY) -> ScriptDirectory:
    """The revisions the migration scripts in `directory` make, read once."""
    return ScriptDirectory.from_config(_config(directory))


def serving_refusal(engine: Engine, history: ScriptDirectory) -> RefusalReason | None:
    """Why this release must not serve the database, or None when it may.

    A database that does not answer is refused. So is one with no revision row,
    which was never migrated, and one at a revision of this release's own
    `history` other than its head, which an older release migrated: serving
    either would read tables or columns that may not exist. A revision this
    release does not know is a newer release's, which only added, so it is
    served. The reasons name the kind of failure and never what the driver says,
    which may quote the address the connection used.
    """
    try:
        with engine.connect() as connection:
            held = set(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError:
        return RefusalReason.NOT_ANSWERING
    if not held:
        return RefusalReason.NO_REVISION
    if history.get_current_head() in held or not held <= _known_revisions(history):
        return None
    return RefusalReason.BEHIND_THIS_RELEASE


def database_is_ready(engine: Engine, history: ScriptDirectory) -> bool:
    """Whether the database answers and this release may serve it; a no is logged."""
    refusal = serving_refusal(engine, history)
    if refusal is not None:
        _log.info("database_readiness.refused", extra={"reason": refusal})
    return refusal is None


def _config(directory: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(directory))
    return config


def _known_revisions(history: ScriptDirectory) -> set[str]:
    return {script.revision for script in history.walk_revisions()}


def _is_from_a_newer_release(connection: Connection, history: ScriptDirectory) -> bool:
    held = MigrationContext.configure(connection).get_current_heads()
    # The read opened a transaction, which would stop the migration from owning its own.
    connection.rollback()
    return not set(held) <= _known_revisions(history)


def _hold(connection: Connection, lock: AdvisoryLock) -> None:
    connection.execute(_TAKE_LOCK, parameters(lock))
    # The lock outlives the commit: it belongs to the session, not the transaction.
    connection.commit()


def _release(connection: Connection, lock: AdvisoryLock) -> None:
    connection.execute(_RELEASE_LOCK, parameters(lock))
    connection.commit()
