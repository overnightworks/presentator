"""The migrations of the database, and the rule for serving what they made."""

import logging
import socket
from enum import StrEnum
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, Engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from presentator.adapters.postgres.engine import CONNECT_TIMEOUT_SECONDS
from presentator.adapters.postgres.locks import MIGRATION_LOCK, AdvisoryLock, parameters

MIGRATIONS_DIRECTORY: Final = Path(__file__).parent / "migrations"
_TAKE_LOCK: Final = text("SELECT pg_advisory_lock(:lock_class, :instance)")
_RELEASE_LOCK: Final = text("SELECT pg_advisory_unlock(:lock_class, :instance)")
# The port libpq connects to when the URL names none.
_POSTGRES_PORT: Final = 5432
_log = logging.getLogger(__name__)


class RefusalReason(StrEnum):
    """Why a database is not served or migrated: the closed set of `reason` codes."""

    NOT_ANSWERING = "not_answering"
    LOGIN_REFUSED = "login_refused"
    NO_REVISION = "no_revision"
    BEHIND_THIS_RELEASE = "behind_this_release"
    NEWER_THAN_THIS_RELEASE = "newer_than_this_release"


class DatabaseRefusedError(Exception):
    """The database is not served or migrated, for `reason`.

    It carries the code and the class name of the driver's error, if one refused
    it, and never what the driver says, which may quote the address the
    connection used.
    """

    def __init__(
        self, reason: RefusalReason, *, driver_error: str | None = None
    ) -> None:
        """Refuse the database for `reason`, after `driver_error` if one ended it."""
        super().__init__(reason)
        self.reason = reason
        self.driver_error = driver_error


def _not_answering(error: SQLAlchemyError) -> DatabaseRefusedError:
    return DatabaseRefusedError(
        RefusalReason.NOT_ANSWERING, driver_error=_driver_error_name(error)
    )


def _refused_at_connect(engine: Engine, error: SQLAlchemyError) -> DatabaseRefusedError:
    """A login the server refused, or a server that is not there to answer.

    libpq gives a failed connect no SQLSTATE and words its message in the
    server's language, so whether the server's port accepts a plain connection
    tells a refused login (password, role or database) from no server at all.
    """
    if isinstance(error, OperationalError) and _port_accepts(engine.url):
        return DatabaseRefusedError(
            RefusalReason.LOGIN_REFUSED, driver_error=_driver_error_name(error)
        )
    return _not_answering(error)


def _port_accepts(url: URL) -> bool:
    """Whether the host and port of `url` accept a TCP connection."""
    if url.host is None:
        return False
    address = (url.host, url.port or _POSTGRES_PORT)
    try:
        with socket.create_connection(address, timeout=CONNECT_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _driver_error_name(error: SQLAlchemyError) -> str:
    # SQLAlchemy wraps the driver's own error, whose class names the failure.
    return type(getattr(error, "orig", None) or error).__name__


def cap_alembic_logging() -> None:
    """Keep alembic's own progress text out of the JSON log; it is free text there."""
    logging.getLogger("alembic").setLevel(logging.WARNING)


def migrate(engine: Engine) -> None:
    """Bring the database to head; concurrent runs wait for one another.

    Migrations only add, so a database whose revision this release does not know
    was migrated by a newer release: it stays as it is, which lets a rollback of
    the release run its migration step without harm. A database that does not
    answer, or refuses the login, is refused.
    """
    config = _config(MIGRATIONS_DIRECTORY)
    history = ScriptDirectory.from_config(config)
    try:
        connection = engine.connect()
    except SQLAlchemyError as error:
        raise _refused_at_connect(engine, error) from None
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


def serving_refusal(
    engine: Engine, history: ScriptDirectory
) -> DatabaseRefusedError | None:
    """Why this release must not serve the database, or None when it may.

    A database that does not answer, or refuses the login, is refused. So is
    one with no revision row, which was never migrated, and one at a revision
    of this release's own `history` other than its head, which an older
    release migrated: serving either would read tables or columns that may not
    exist. A revision this release does not know is a newer release's, which
    only added, so it is served. The reasons name the kind of failure and never
    what the driver says, which may quote the address the connection used.
    """
    try:
        connection = engine.connect()
    except SQLAlchemyError as error:
        return _refused_at_connect(engine, error)
    try:
        with connection:
            held = set(MigrationContext.configure(connection).get_current_heads())
    except SQLAlchemyError as error:
        return _not_answering(error)
    if not held:
        return DatabaseRefusedError(RefusalReason.NO_REVISION)
    if history.get_current_head() in held or not held <= _known_revisions(history):
        return None
    return DatabaseRefusedError(RefusalReason.BEHIND_THIS_RELEASE)


def database_is_ready(engine: Engine, history: ScriptDirectory) -> bool:
    """Whether the database answers and this release may serve it; a no is logged."""
    refusal = serving_refusal(engine, history)
    if refusal is not None:
        _log.info("database_readiness.refused", extra={"reason": refusal.reason})
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
