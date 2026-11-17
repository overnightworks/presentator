"""Throwaway databases on the server a test run is given.

CI's service or a developer's container is the server; every test gets a
database of its own on it, so tests stay parallel-safe.
"""

import os
import uuid
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Final

import pytest
from pydantic import SecretStr, TypeAdapter
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.migrate import migrate
from tests.support.two_processes import Finished, release_together, two_modules

SERVER_VARIABLE = "PRESENTATOR_TEST_POSTGRES_URL"
_NO_SERVER = (
    f"{SERVER_VARIABLE} names no Postgres server: set it to a postgresql URL of a"
    " server whose user may create databases"
)
# Nothing listens here, so a start that reaches it fails at once; tests that
# never reach the database name it.
NO_DATABASE_HERE = "postgresql://nobody@127.0.0.1:1/nothing"
# A trust-authenticated test server asks for no password, so any text will do.
_ANY_PASSWORD = SecretStr("not-checked-by-the-test-server")


class _AnyWriteStands:
    """A fence that refuses nothing, for the tests of what a store writes."""

    def check(self, connection: Connection) -> None:
        """Take every write for the holder's."""


NO_FENCE: Final = _AnyWriteStands()


def _server() -> URL:
    server = os.environ.get(SERVER_VARIABLE)
    if not server:
        pytest.fail(_NO_SERVER, pytrace=False)
    return make_url(server).set(drivername="postgresql+psycopg")


def engine_on(database_url: str) -> Engine:
    """An engine on a throwaway database."""
    return create_database_engine(database_url, _ANY_PASSWORD)


@contextmanager
def created_database(template: str | None = None) -> Generator[str]:
    """A database of its own, copied from `template` if named, dropped after."""
    name = f"presentator_{uuid.uuid4().hex}"
    server = _server()
    # CREATE and DROP DATABASE cannot run inside a transaction.
    administration = create_engine(server, isolation_level="AUTOCOMMIT")
    from_template = f' TEMPLATE "{template}"' if template else ""
    try:
        with administration.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{name}"{from_template}'))
        try:
            # The URL as a deployment writes it: no driver, no password.
            yield server.set(
                drivername="postgresql", database=name, password=None
            ).render_as_string(hide_password=False)
        finally:
            with administration.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    finally:
        administration.dispose()


@contextmanager
def migrated_template() -> Generator[str]:
    """The name of a database at head, which a test database is copied from."""
    with created_database() as database_url:
        engine = engine_on(database_url)
        try:
            migrate(engine)
        finally:
            engine.dispose()
        name = make_url(database_url).database
        assert name is not None
        yield name


# The server's lock table covers every database on it, so a test counts only its own.
_THIS_DATABASE: Final = (
    "database = (SELECT oid FROM pg_database WHERE datname = current_database())"
)
_GRANTED_ADVISORY: Final = (
    "FROM pg_locks WHERE locktype = 'advisory' AND granted"
    f" AND {_THIS_DATABASE} AND classid::int = ANY(:classes)"
)
_COUNT_GRANTED: Final = text(f"SELECT count(*) {_GRANTED_ADVISORY}")
_END_HOLDERS: Final = text(f"SELECT pg_terminate_backend(pid) {_GRANTED_ADVISORY}")
_COUNT_WAITING: Final = text(
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
    " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
)


def granted_advisory_locks(engine: Engine, *classes: IntEnum) -> int:
    """How many advisory locks of these classes the database holds now."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                _COUNT_GRANTED, {"classes": [int(each) for each in classes]}
            ).scalar_one()
        )


def waiting_advisory_locks(engine: Engine) -> int:
    """How many advisory lock requests of the database wait now."""
    with engine.connect() as connection:
        return int(connection.execute(_COUNT_WAITING).scalar_one())


def end_connections_holding(engine: Engine, *classes: IntEnum) -> None:
    """End every connection that holds an advisory lock of these classes."""
    with engine.begin() as connection:
        connection.execute(_END_HOLDERS, {"classes": [int(each) for each in classes]})


def database_environment(database_url: str, directory: Path) -> Mapping[str, str]:
    """What a process reads to reach the database, with the password in a file."""
    password_file = directory / "database-password"
    password_file.write_text(_ANY_PASSWORD.get_secret_value(), encoding="utf-8")
    return {
        "PRESENTATOR_DATABASE_URL": database_url,
        "PRESENTATOR_DATABASE_PASSWORD_FILE": str(password_file),
    }


def point_at(
    environment: pytest.MonkeyPatch, database_url: str, directory: Path
) -> None:
    """Set the variables that make an instance in this process reach the database."""
    for name, value in database_environment(database_url, directory).items():
        environment.setenv(name, value)


def engine_from_environment() -> Engine:
    """In a child: the engine on the database `database_environment` pointed it at."""
    password = Path(os.environ["PRESENTATOR_DATABASE_PASSWORD_FILE"]).read_text(
        encoding="utf-8"
    )
    return create_database_engine(
        os.environ["PRESENTATOR_DATABASE_URL"], SecretStr(password)
    )


@dataclass(frozen=True, slots=True)
class ChildModule:
    """A module whose children run beside the test against its throwaway database."""

    name: str

    def both_report[Reported](
        self,
        database_url: str,
        directory: Path,
        shape: TypeAdapter[Reported],
        *,
        first: str,
        second: str | None = None,
    ) -> tuple[Reported, Reported]:
        """What two children at once reported, both exiting 0.

        Each child reads the database from its environment, says it is ready,
        and prints its result as the last line of its output. The first does
        `first`; the second does `second`, or `first` too.
        """
        with two_modules(
            self.name,
            [first],
            database_environment(database_url, directory),
            second_arguments=None if second is None else [second],
        ) as processes:
            release_together(processes)
            finished = processes.finish()
        assert [each.exit_code for each in finished] == [0, 0], finished
        one, other = (_reported(each, shape) for each in finished)
        return one, other


def _reported[Reported](finished: Finished, shape: TypeAdapter[Reported]) -> Reported:
    return shape.validate_json(finished.output.strip().splitlines()[-1])
