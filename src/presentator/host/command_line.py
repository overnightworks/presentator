"""The commands the `presentator` program takes besides serving."""

import argparse
import logging
from collections.abc import Sequence
from datetime import timedelta
from typing import Final

from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins
from presentator.adapters.postgres.migrate import StoreFault, migrate, store_refusal
from presentator.host.config import load_database_settings
from presentator.host.lifecycle import read_command_line, refuse_argument

MIGRATE_COMMAND = "migrate"
UNPIN_HOST_COMMAND = "unpin-host"
_HOST: Final = "host"
_HOST_UNPINNED: Final = "host_key.unpinned"
_HOST_NOT_PINNED: Final = "host_key.not_pinned"
# Apart from 1, a failed process, and 2, a refused command line.
NOTHING_PINNED_STATUS: Final = 3

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
    """Remove that host's pinned key, its statement bounded as served ones are.

    A host nothing pinned ends with `NOTHING_PINNED_STATUS`; a store fault is
    refused for the reason `store_refusal` names.
    """
    settings = load_database_settings()
    database = settings.postgres
    engine = create_database_engine(
        database.url,
        database.password,
        statement_timeout=timedelta(
            seconds=settings.database_statement_timeout_seconds
        ),
    )
    try:
        removed = PostgresHostKeyPins(engine=engine).unpin(host)
    except StoreFault as fault:
        raise store_refusal(fault, engine.url) from None
    finally:
        engine.dispose()
    if not removed:
        _log.error(_HOST_NOT_PINNED, extra={"host": host})
        raise SystemExit(NOTHING_PINNED_STATUS)
    _log.info(_HOST_UNPINNED, extra={"host": host})


def read_the_command_line(arguments: Sequence[str] | None) -> argparse.Namespace:
    """The command `arguments` ask for; a refused one ends the process in one line.

    The command line checks the host itself: argparse names no argument in the
    refusal of a missing one, and takes an empty word for a host.
    """
    asked = read_command_line(_command_line(), arguments)
    if asked.command == UNPIN_HOST_COMMAND and not asked.host:
        refuse_argument(_HOST)
    return asked


def _command_line() -> argparse.ArgumentParser:
    """The parser of the commands that replace serving."""
    parser = argparse.ArgumentParser(prog="presentator", exit_on_error=False)
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        MIGRATE_COMMAND,
        help="bring the database to the newest revision, then exit",
        exit_on_error=False,
    )
    # The host is optional to argparse alone, so the usage names it as required.
    unpin = commands.add_parser(
        UNPIN_HOST_COMMAND,
        help="remove one host's pinned ssh key after a verified rotation, then exit",
        usage=f"%(prog)s [-h] {_HOST}",
        exit_on_error=False,
    )
    unpin.add_argument(
        _HOST, nargs="?", help="the host as ssh names it, `[host]:port` off 22"
    )
    return parser
