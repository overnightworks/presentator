"""Who may sign in, written straight into a database.

Nothing in the product creates a person yet, so the tests that need an owner or
a session holder put the row there themselves.
"""

import uuid

from sqlalchemy import Engine, text
from webauth.ports import AccountKind

from presentator.contracts.models import User

_SEED_USER = text(
    "INSERT INTO users (id, username, role, kind)"
    " VALUES (:id, :username, :role, :kind)"
    " ON CONFLICT DO NOTHING"
)


def a_session_id() -> str:
    """An id no other call of the test run returns."""
    return uuid.uuid4().hex


def seed_user(engine: Engine, person: User) -> None:
    """Write the person's account row, leaving one that stands alone."""
    with engine.begin() as connection:
        connection.execute(
            _SEED_USER,
            {
                "id": person.id,
                "username": person.username,
                "role": person.role.value,
                "kind": AccountKind.PERSON.value,
            },
        )
