"""The sessions table is the shape webauth's Postgres extra publishes, and only that."""

from collections.abc import Iterator

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.schema import SchemaItem
from webauth.postgres import stored_key

from presentator.adapters.postgres.migrate import MIGRATIONS_DIRECTORY, migrate
from presentator.adapters.postgres.session_schema import SESSIONS
from tests.support.postgres import created_database, engine_on
from tests.support.sign_in_support import a_scene

_KEPT_TABLES = ("users", "host_key_pins")
_THE_OLD_SHAPE = (
    "INSERT INTO sessions (id, user_id, created_at, expires_at, refresh_token)"
    " VALUES ('old-session-id', 'u1', now(), now() + interval '1 hour', 'rt')"
)


def _only_the_sessions_table(
    item: SchemaItem,
    name: str | None,
    type_: str,
    reflected: bool,  # noqa: FBT001, ARG001 - alembic fixes this signature
    compare_to: SchemaItem | None,  # noqa: ARG001
) -> bool:
    if type_ == "table":
        return name == SESSIONS.name
    return getattr(item, "table", SESSIONS).name == SESSIONS.name


@pytest.fixture
def database_before_the_extra() -> Iterator[Engine]:
    """A database one revision before the extra's table, holding a live session."""
    with created_database() as database_url:
        engine = engine_on(database_url)
        try:
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
            with engine.begin() as connection:
                config.attributes["connection"] = connection
                command.upgrade(config, "0004")
                connection.execute(
                    text(
                        "INSERT INTO users (id, username, role)"
                        " VALUES ('u1', 'ada', 'admin')"
                    )
                )
                connection.execute(
                    text(
                        "INSERT INTO host_key_pins (host, public_key)"
                        " VALUES ('git.example', 'ssh-ed25519 A')"
                    )
                )
                connection.execute(text(_THE_OLD_SHAPE))
            yield engine
        finally:
            engine.dispose()


def _rows_of(engine: Engine, tables: tuple[str, ...]) -> dict[str, list[object]]:
    with engine.connect() as connection:
        return {
            table: connection.execute(text(f"SELECT * FROM {table}")).all()  # noqa: S608 - fixed names
            for table in tables
        }


def test_the_migrated_sessions_table_equals_the_one_the_extra_publishes(
    engine: Engine,
) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={
                "include_object": _only_the_sessions_table,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        difference = compare_metadata(context, SESSIONS.metadata)

    assert difference == []


def test_no_session_row_holds_the_id_of_a_live_cookie(engine: Engine) -> None:
    scene = a_scene(engine)
    browser = scene.browser()
    scene.sign_in(browser)

    session_id = scene.session_id_of(browser)

    with engine.connect() as connection:
        stored = (
            connection.execute(text("SELECT id_hash FROM sessions")).scalars().all()
        )
    assert stored == [stored_key(session_id)]


def test_migrating_over_live_sessions_ends_every_one_and_keeps_every_other_row(
    database_before_the_extra: Engine,
) -> None:
    kept_before = _rows_of(database_before_the_extra, _KEPT_TABLES)

    migrate(database_before_the_extra)

    assert _rows_of(database_before_the_extra, ("sessions",)) == {"sessions": []}
    assert _rows_of(database_before_the_extra, _KEPT_TABLES) == kept_before
