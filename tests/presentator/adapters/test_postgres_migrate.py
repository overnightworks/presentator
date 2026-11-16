"""`migrate` brings a database to head and leaves a newer one alone."""

from collections.abc import Callable

import psycopg
import pytest
from sqlalchemy import Engine, inspect, make_url, text

from presentator.adapters.postgres.migrate import (
    DatabaseRefusedError,
    migrate,
    migration_history,
    serving_refusal,
)
from tests.support.log_lines import json_logged
from tests.support.postgres import engine_on

_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS = "9999"


def test_an_empty_database_gets_every_table(unmigrated_database_url: str) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        migrate(engine)
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert tables == {
        "alembic_version",
        "users",
        "sessions",
        "instance_settings",
        "person_preferences",
        "sources",
        "source_key_drafts",
        "decks",
        "source_runs",
        "refresh_owner",
    }


def test_a_migrated_database_holds_the_one_refresh_owner_row(
    unmigrated_database_url: str,
) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        migrate(engine)
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT id FROM refresh_owner")).all()
    finally:
        engine.dispose()

    assert [tuple(row) for row in rows] == [(1,)]


Reading = dict[str, tuple[list[tuple[str, str, bool]], list[tuple[object, ...]]]]


def _reading_of(engine: Engine) -> Reading:
    """Every table's rows and the columns of the schema, as one comparable reading."""
    inspector = inspect(engine)
    reading: Reading = {}
    with engine.connect() as connection:
        for table in sorted(inspector.get_table_names()):
            columns = [
                (column["name"], str(column["type"]), column["nullable"])
                for column in inspector.get_columns(table)
            ]
            rows = connection.execute(text(f'SELECT * FROM "{table}" ORDER BY 1')).all()  # noqa: S608 - names read from the database itself
            reading[table] = (columns, [tuple(row) for row in rows])
    return reading


def test_a_second_migration_changes_nothing(unmigrated_database_url: str) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        migrate(engine)
        with engine.begin() as connection:
            connection.execute(text("UPDATE refresh_owner SET epoch = epoch + 1"))
        before = _reading_of(engine)

        migrate(engine)
        after = _reading_of(engine)
    finally:
        engine.dispose()

    assert after == before


def test_a_database_a_newer_release_migrated_is_left_as_it_is(
    unmigrated_database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"),
                {"revision": _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS},
            )

        with json_logged(capsys) as logged:
            migrate(engine)

        inspector = inspect(engine)
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert revision == _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS
    assert tables == {"alembic_version"}
    (refusal,) = [one for one in logged.lines if one["event"] == "migration.refused"]
    assert refusal["reason"] == "newer_than_this_release"
    assert refusal["level"] == "info"


def _refusal_of_migrating(engine: Engine) -> DatabaseRefusedError | None:
    try:
        migrate(engine)
    except DatabaseRefusedError as refused:
        return refused
    return None


def _refusal_of_serving(engine: Engine) -> DatabaseRefusedError | None:
    return serving_refusal(engine, migration_history())


type RefusalOf = Callable[[Engine], DatabaseRefusedError | None]
_ENTRIES = pytest.mark.parametrize(
    "refusal_of", [_refusal_of_migrating, _refusal_of_serving]
)


def _refusal_on(url: str, refusal_of: RefusalOf) -> DatabaseRefusedError | None:
    engine = engine_on(url)
    try:
        return refusal_of(engine)
    finally:
        engine.dispose()


@_ENTRIES
def test_a_server_that_refuses_the_login_is_refused_as_login_refused(
    unmigrated_database_url: str, refusal_of: RefusalOf
) -> None:
    # The server answers, but holds no database of this name.
    absent = make_url(unmigrated_database_url).set(database="presentator_absent")

    refused = _refusal_on(absent.render_as_string(), refusal_of)

    assert refused is not None
    assert (refused.reason, refused.driver_error) == (
        "login_refused",
        psycopg.OperationalError.__name__,
    )


@_ENTRIES
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://nobody@127.0.0.1:1/nothing",
        # RFC 6761 keeps `.invalid` from ever resolving.
        "postgresql://nobody@database.invalid/nothing",
    ],
)
def test_a_server_that_is_not_there_is_refused_as_not_answering(
    url: str, refusal_of: RefusalOf
) -> None:
    refused = _refusal_on(url, refusal_of)

    assert refused is not None
    assert (refused.reason, refused.driver_error) == (
        "not_answering",
        psycopg.OperationalError.__name__,
    )


def test_a_database_whose_revision_cannot_be_read_is_refused_as_not_answering(
    unmigrated_database_url: str,
) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            # The revision table is there, but without the column alembic reads.
            connection.execute(text("CREATE TABLE alembic_version (other integer)"))

        refused = _refusal_of_serving(engine)
    finally:
        engine.dispose()

    assert refused is not None
    assert (refused.reason, refused.driver_error) == (
        "not_answering",
        psycopg.errors.UndefinedColumn.__name__,
    )
