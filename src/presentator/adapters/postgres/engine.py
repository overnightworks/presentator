"""The one way this product opens a Postgres connection."""

from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Final

from pydantic import SecretStr
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.engine import make_url

POSTGRES_DRIVER: Final = "postgresql+psycopg"
# Bounds a connection attempt on a database that is down or silent; libpq takes
# whole seconds.
CONNECT_TIMEOUT_SECONDS: Final = 5
_MILLISECONDS: Final = 1000


def create_database_engine(
    database_url: str, password: SecretStr, *, statement_timeout: timedelta | None
) -> Engine:
    """An engine on psycopg 3 for a URL without a password, which is set in.

    Every session it opens cancels a statement that runs past
    `statement_timeout`; None leaves statements unbounded, for the migrations.
    """
    url = make_url(database_url).set(
        drivername=POSTGRES_DRIVER, password=password.get_secret_value()
    )
    connect_args: dict[str, object] = {"connect_timeout": CONNECT_TIMEOUT_SECONDS}
    if statement_timeout is not None:
        connect_args["options"] = (
            f"-c statement_timeout={milliseconds_of(statement_timeout)}"
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args=connect_args,
        # A failed statement's error would otherwise quote its parameters, among
        # them refresh tokens, sealed secrets and personal data, to every log.
        hide_parameters=True,
    )


def connection_or_inside(
    engine: Engine, inside: Connection | None
) -> AbstractContextManager[Connection]:
    """The open transaction's connection, or a fresh one that commits on its way out."""
    return engine.begin() if inside is None else nullcontext(inside)


def in_utc(instant: datetime) -> datetime:
    """The instant in UTC, whatever time zone the connection reads timestamps in."""
    return instant.astimezone(UTC)


def milliseconds_of(bound: timedelta) -> int:
    """A bound in the whole milliseconds a Postgres timeout takes, at least one."""
    return max(1, ceil(bound.total_seconds() * _MILLISECONDS))
