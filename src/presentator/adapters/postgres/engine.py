"""The one way this product opens a Postgres connection."""

from typing import Final

from pydantic import SecretStr
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

POSTGRES_DRIVER: Final = "postgresql+psycopg"
# Bounds a connection attempt on a database that is down or silent; libpq takes
# whole seconds.
CONNECT_TIMEOUT_SECONDS: Final = 5


def create_database_engine(database_url: str, password: SecretStr) -> Engine:
    """An engine on psycopg 3 for a URL without a password, which is set in."""
    url = make_url(database_url).set(
        drivername=POSTGRES_DRIVER, password=password.get_secret_value()
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": CONNECT_TIMEOUT_SECONDS},
    )
