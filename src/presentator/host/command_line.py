"""The commands the `presentator` program takes besides serving."""

import argparse
import logging
from typing import Final

from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins
from presentator.adapters.postgres.migrate import migrate
from presentator.host.config import load_database_settings

MIGRATE_COMMAND = "migrate"
UNPIN_HOST_COMMAND = "unpin-host"
_HOST_UNPINNED: Final = "host_key.unpinned"
_HOST_NOT_PINNED: Final = "host_key.not_pinned"

_log = logging.getLogger(__name__)


def migrate_the_database() -> None:
    """Bring the configured database to the newest revision."""
    database = load_database_settings().postgres
    # A migration, and its wait for another, run as long as they take.
    engine = create_database_engine(
        database.url, database.password, statement_timeout=None
    )
    try:
        migrate(engine)
    finally:
        engine.dispose()


def unpin_the_host(host: str) -> None:
    """Remove that host's pinned key; a host nothing pinned ends with status 1."""
    database = load_database_settings().postgres
    # A one-row delete by hand waits for a pin in flight rather than giving up.
    engine = create_database_engine(
        database.url, database.password, statement_timeout=None
    )
    try:
        removed = PostgresHostKeyPins(engine=engine).unpin(host)
    finally:
        engine.dispose()
    if not removed:
        _log.error(_HOST_NOT_PINNED, extra={"host": host})
        raise SystemExit(1)
    _log.info(_HOST_UNPINNED, extra={"host": host})


def command_line() -> argparse.ArgumentParser:
    """The parser of the commands that replace serving."""
    parser = argparse.ArgumentParser(prog="presentator")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        MIGRATE_COMMAND, help="bring the database to the newest revision, then exit"
    )
    unpin = commands.add_parser(
        UNPIN_HOST_COMMAND,
        help="remove one host's pinned ssh key after a verified rotation, then exit",
    )
    unpin.add_argument("host", help="the host as ssh names it, `[host]:port` off 22")
    return parser
