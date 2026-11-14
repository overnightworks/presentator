"""`migrate` brings a database to head and leaves a newer one alone."""

from sqlalchemy import inspect, text

from presentator.adapters.postgres.locks import MIGRATION_LOCK, AdvisoryLock
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
    }


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


def test_a_lock_of_the_same_class_for_another_instance_is_free_while_a_migration_runs(
    unmigrated_database_url: str,
) -> None:
    take = text("SELECT pg_try_advisory_lock(:lock_class, :instance)")
    another_instance = AdvisoryLock(
        MIGRATION_LOCK.lock_class, MIGRATION_LOCK.instance + 1
    )
    engine = engine_on(unmigrated_database_url)
    try:
        with engine.connect() as migration, engine.connect() as other:
            migration.execute(
                take,
                {
                    "lock_class": int(MIGRATION_LOCK.lock_class),
                    "instance": MIGRATION_LOCK.instance,
                },
            )
            taken = other.execute(
                take,
                {
                    "lock_class": int(another_instance.lock_class),
                    "instance": another_instance.instance,
                },
            ).scalar_one()
    finally:
        engine.dispose()

    assert taken is True
