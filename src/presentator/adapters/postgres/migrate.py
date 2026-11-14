"""Bring the database to the newest revision this release knows."""

import logging
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, text

from presentator.adapters.postgres.locks import MIGRATION_LOCK, AdvisoryLock, parameters

MIGRATIONS_DIRECTORY: Final = Path(__file__).parent / "migrations"
_TAKE_LOCK: Final = text("SELECT pg_advisory_lock(:lock_class, :instance)")
_RELEASE_LOCK: Final = text("SELECT pg_advisory_unlock(:lock_class, :instance)")
_log = logging.getLogger(__name__)


def migrate(engine: Engine) -> None:
    """Bring the database to head; concurrent runs wait for one another.

    Migrations only add, so a database whose revision this release does not know
    was migrated by a newer release: it stays as it is, which lets a rollback of
    the release run its migration step without harm.
    """
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    with engine.connect() as connection:
        _hold(connection, MIGRATION_LOCK)
        try:
            if _is_from_a_newer_release(connection, config):
                _log.warning("The database is newer than this release; left as it is")
                return
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        finally:
            connection.rollback()
            _release(connection, MIGRATION_LOCK)


def _is_from_a_newer_release(connection: Connection, config: Config) -> bool:
    known = {
        script.revision
        for script in ScriptDirectory.from_config(config).walk_revisions()
    }
    held = MigrationContext.configure(connection).get_current_heads()
    # The read opened a transaction, which would stop the migration from owning its own.
    connection.rollback()
    return not set(held) <= known


def _hold(connection: Connection, lock: AdvisoryLock) -> None:
    connection.execute(_TAKE_LOCK, parameters(lock))
    # The lock outlives the commit: it belongs to the session, not the transaction.
    connection.commit()


def _release(connection: Connection, lock: AdvisoryLock) -> None:
    connection.execute(_RELEASE_LOCK, parameters(lock))
    connection.commit()
