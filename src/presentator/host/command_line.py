"""The commands the `presentator` program takes besides serving."""

import argparse

from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.migrate import migrate
from presentator.host.config import load_database_settings

MIGRATE_COMMAND = "migrate"


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


def command_line() -> argparse.ArgumentParser:
    """The parser of the commands that replace serving."""
    parser = argparse.ArgumentParser(prog="presentator")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser(
        MIGRATE_COMMAND, help="bring the database to the newest revision, then exit"
    )
    return parser
