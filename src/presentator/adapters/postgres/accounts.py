"""The accounts table on Postgres, behind webauth's `SubjectUserStore`."""

from dataclasses import dataclass
from typing import Final

from psycopg.errors import UniqueViolation
from sqlalchemy import Connection, Engine, text
from sqlalchemy.exc import IntegrityError
from webauth.ports import AccountKind, UsernameTakenError

from presentator.adapters.identity import StoredAccount, stored_account
from presentator.adapters.postgres.engine import connection_or_inside
from presentator.contracts.models import Role

_SELECT_ACCOUNT: Final = text(
    "SELECT id, username, role, kind FROM users WHERE id = :user_id"
)
_INSERT_ACCOUNT: Final = text(
    "INSERT INTO users (id, username, role, kind)"
    " VALUES (:user_id, :username, :role, :kind)"
)
_UPDATE_ROLE: Final = text(
    "UPDATE users SET role = :role WHERE id = :user_id"
    " RETURNING id, username, role, kind"
)
_UPDATE_KIND: Final = text(
    "UPDATE users SET kind = :kind WHERE id = :user_id"
    " RETURNING id, username, role, kind"
)


def read_account(connection: Connection, user_id: str) -> StoredAccount | None:
    """The account whose id is `user_id` character for character."""
    row = connection.execute(_SELECT_ACCOUNT, {"user_id": user_id}).fetchone()
    return None if row is None else stored_account(row)


@dataclass(frozen=True, slots=True)
class PostgresSubjectUserStore:
    """The accounts table, keyed by the identity provider's subject.

    Ids compare exactly: Postgres compares equal text byte for byte under a
    deterministic collation, so a subject that differs only in case is another
    account. Every call commits on its own, except on a store that
    `PostgresIdentityWrites` bound to the connection of its open transaction.
    """

    engine: Engine
    inside: Connection | None = None

    def get(self, user_id: str) -> StoredAccount | None:
        """The account whose id is `user_id` character for character."""
        with connection_or_inside(self.engine, self.inside) as connection:
            return read_account(connection, user_id)

    def create_for_subject(
        self, subject: str, username: str, role: str, kind: AccountKind
    ) -> StoredAccount:
        """Add the account of `subject`; only a username another holds is 'taken'."""
        with connection_or_inside(self.engine, self.inside) as connection:
            try:
                # A refused insert must not abort the entry's transaction.
                with connection.begin_nested():
                    connection.execute(
                        _INSERT_ACCOUNT,
                        {
                            "user_id": subject,
                            "username": username,
                            "role": role,
                            "kind": kind.value,
                        },
                    )
            except IntegrityError as refused:
                if not isinstance(refused.orig, UniqueViolation):
                    raise
                if read_account(connection, subject) is not None:
                    raise
                message = "the username is held by another account"
                raise UsernameTakenError(message) from None
        return StoredAccount(id=subject, username=username, role=Role(role), kind=kind)

    def set_role(self, user_id: str, role: str) -> StoredAccount:
        """Change the role of one existing account; no such account raises."""
        with connection_or_inside(self.engine, self.inside) as connection:
            row = connection.execute(
                _UPDATE_ROLE, {"user_id": user_id, "role": role}
            ).fetchone()
        if row is None:
            message = "no account to change the role of"
            raise LookupError(message)
        return stored_account(row)

    def set_kind(self, user_id: str, kind: AccountKind) -> StoredAccount:
        """Change the kind of one existing account; no such account raises."""
        with connection_or_inside(self.engine, self.inside) as connection:
            row = connection.execute(
                _UPDATE_KIND, {"user_id": user_id, "kind": kind.value}
            ).fetchone()
        if row is None:
            message = "no account to change the kind of"
            raise LookupError(message)
        return stored_account(row)
