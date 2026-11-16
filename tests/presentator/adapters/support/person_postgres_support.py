"""Two people's rows over real Postgres and real mirror and build directories."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import Connection, Engine, text
from webauth.ports import AccountKind

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.preferences import PostgresPersonPreferencesStore
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.adapters.postgres.source_runs import PostgresSourceRunStore
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.models import User
from tests.presentator.adapters.support.key_draft_support import INSTANCE_KEY
from tests.presentator.adapters.support.person_instance_support import (
    Directories,
    Stores,
    directories_with_both_people,
)
from tests.support.postgres import NO_FENCE, engine_on

_ACCOUNTS = "users"


@dataclass
class PostgresInstance(Directories):
    """A Postgres database and the directories beside it, holding two people's rows."""

    engine: Engine


def _seed_user(engine: Engine) -> Callable[[User], None]:
    def seed(person: User) -> None:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, username, role, kind)"
                    " VALUES (:id, :username, :role, :kind)"
                    " ON CONFLICT DO NOTHING"
                ),
                {
                    "id": person.id,
                    "username": person.username,
                    "role": person.role.value,
                    "kind": AccountKind.PERSON.value,
                },
            )

    return seed


def _stores_over(engine: Engine) -> Stores:
    return Stores(
        seed_user=_seed_user(engine),
        sources=PostgresSourceStore(
            engine=engine,
            identifiers=TokenIdentifierFactory(),
            box=secret_box(INSTANCE_KEY),
            fence=NO_FENCE,
        ),
        decks=PostgresDeckStore(engine=engine, fence=NO_FENCE),
        runs=PostgresSourceRunStore(engine=engine, fence=NO_FENCE),
        key_drafts=PostgresSourceKeyDrafts(
            engine=engine,
            identifiers=TokenIdentifierFactory(),
            box=secret_box(INSTANCE_KEY),
        ),
        accounts=PostgresIdentityWrites(engine, LoggingAuditSink()),
        sessions=PostgresSessionRecordStore(engine),
        preferences=PostgresPersonPreferencesStore(engine),
    )


@contextmanager
def an_instance_with_both_people_on_postgres(
    tmp_path: Path, database_url: str
) -> Generator[PostgresInstance]:
    """Both people's rows in a migrated database, and the directories beside it."""
    engine = engine_on(database_url)
    try:
        around = directories_with_both_people(_stores_over(engine), tmp_path)
        yield PostgresInstance(
            engine=engine,
            mirrors=around.mirrors,
            builds=around.builds,
            decks=around.decks,
            people=around.people,
            kept_by=around.kept_by,
        )
    finally:
        engine.dispose()


# Finding, by the schema's own foreign keys, every row a person owns.

_FOREIGN_KEYS = text(
    "SELECT child.relname, child_column.attname, parent.relname, parent_column.attname"
    " FROM pg_constraint link"
    " JOIN pg_class child ON child.oid = link.conrelid"
    " JOIN pg_class parent ON parent.oid = link.confrelid"
    " JOIN pg_attribute child_column ON child_column.attrelid = link.conrelid"
    "  AND child_column.attnum = link.conkey[1]"
    " JOIN pg_attribute parent_column ON parent_column.attrelid = link.confrelid"
    "  AND parent_column.attnum = link.confkey[1]"
    " WHERE link.contype = 'f' AND child.relnamespace = 'public'::regnamespace"
)
_PRIMARY_KEYS = text(
    "SELECT class.relname, key_column.attname"
    " FROM pg_constraint key"
    " JOIN pg_class class ON class.oid = key.conrelid"
    " JOIN pg_attribute key_column ON key_column.attrelid = key.conrelid"
    "  AND key_column.attnum = key.conkey[1]"
    " WHERE key.contype = 'p' AND class.relnamespace = 'public'::regnamespace"
)
_COLUMNS_NAMING_AN_ACCOUNT = text(
    "SELECT DISTINCT table_name FROM information_schema.columns"
    " WHERE table_schema = 'public' AND column_name IN ('user_id', 'owner_id')"
)


class ForeignKey(NamedTuple):
    """A single-column reference of one table's column to another table's column."""

    child: str
    column: str
    parent: str
    target: str


def _foreign_keys(connection: Connection) -> list[ForeignKey]:
    return [ForeignKey(*row) for row in connection.execute(_FOREIGN_KEYS)]


def tables_reaching_accounts(connection: Connection) -> set[str]:
    """Every table that references `users`, or names a `user_id` or `owner_id`."""
    references = _foreign_keys(connection)
    reached = {_ACCOUNTS} | {
        name for (name,) in connection.execute(_COLUMNS_NAMING_AN_ACCOUNT)
    }
    grew = True
    while grew:
        grew = False
        for link in references:
            if link.child not in reached and link.parent in reached:
                reached.add(link.child)
                grew = True
    return reached


def owned_row_keys(connection: Connection, person_id: str) -> dict[str, set[str]]:
    """Per table that reaches `users`, the primary keys the person owns.

    Taken before an erasure, these name rows that stay findable after their
    parents are gone, so an orphan is still counted.
    """
    reaching = tables_reaching_accounts(connection)
    keys = dict(connection.execute(_PRIMARY_KEYS).all())
    links = [
        link
        for link in _foreign_keys(connection)
        if link.child in reaching and link.parent in reaching
    ]
    owned: dict[str, set[str]] = {table: set() for table in reaching}
    owned[_ACCOUNTS] = {
        account
        for (account,) in connection.execute(
            text("SELECT id FROM users WHERE id = :person_id"), {"person_id": person_id}
        )
    }
    grew = True
    while grew:
        grew = False
        for link in links:
            found = _following(connection, link, owned[link.parent], keys)
            if not found <= owned[link.child]:
                owned[link.child] |= found
                grew = True
    return owned


def _following(
    connection: Connection,
    link: ForeignKey,
    parent_keys: set[str],
    keys: dict[str, str],
) -> set[str]:
    query = text(
        f'SELECT "{keys[link.child]}"::text FROM "{link.child}"'  # noqa: S608 - names read from the catalog
        f' WHERE "{link.column}"::text IN'
        f' (SELECT "{link.target}"::text FROM "{link.parent}"'
        f' WHERE "{keys[link.parent]}"::text = ANY(:parent_keys))'
    )
    return {
        found
        for (found,) in connection.execute(query, {"parent_keys": sorted(parent_keys)})
    }


def rows_still_there(
    connection: Connection, owned: dict[str, set[str]]
) -> dict[str, int]:
    """Per table, how many of the previously owned primary keys still exist."""
    keys = dict(connection.execute(_PRIMARY_KEYS).all())
    return {
        table: connection.execute(
            text(
                f'SELECT COUNT(*) FROM "{table}"'  # noqa: S608 - names read from the catalog
                f' WHERE "{keys[table]}"::text = ANY(:owned)'
            ),
            {"owned": sorted(ids)},
        ).scalar_one()
        for table, ids in owned.items()
    }
