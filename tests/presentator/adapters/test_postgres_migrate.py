"""`migrate` brings a database to head and leaves a newer one alone."""

from sqlalchemy import inspect, text

from presentator.adapters.postgres.migrate import migrate
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


def test_a_second_migration_changes_nothing(unmigrated_database_url: str) -> None:
    engine = engine_on(unmigrated_database_url)
    try:
        migrate(engine)
        migrate(engine)
        with engine.connect() as connection:
            revisions = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).all()
    finally:
        engine.dispose()

    assert len(revisions) == 1


def test_a_database_a_newer_release_migrated_is_left_as_it_is(
    unmigrated_database_url: str,
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
