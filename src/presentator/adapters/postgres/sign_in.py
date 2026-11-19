"""The sign-in transaction on Postgres, behind webauth's `SignInTransaction`."""

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, func, select, text
from webauth.ports import AuditSink, SignInStores
from webauth.postgres import database_errors_as_store_errors, sign_in_entry

from presentator.adapters.identity import lobby_user
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.locks import SIGN_IN_ENTRY_KEY
from presentator.adapters.postgres.session_schema import SESSIONS
from presentator.contracts.models import User

_DELETE_ACCOUNT: Final = text("DELETE FROM users WHERE id = :user_id")


@dataclass(frozen=True, slots=True)
class PostgresIdentityWrites:
    """Every identity write, each one atomic, serialized across processes.

    Calling it enters webauth's `SignInTransaction`: one transaction over accounts
    and sessions that first takes the sign-in advisory lock, so entries of any
    number of processes run one after another until the entry has left, and that
    rolls back when the block raises. The audit events of the entry reach the sink
    when the block returns, still under the lock and before COMMIT, so a block that
    raises leaves no line; a sink that raises rolls the entry back. Deleting an
    account takes the same lock and transaction.
    """

    engine: Engine
    audit: AuditSink

    def __call__(self) -> AbstractContextManager[SignInStores]:
        """Enter one sign-in transaction; its stores share the entry's connection."""
        return self._entry()

    @contextmanager
    def _entry(self) -> Generator[SignInStores]:
        with (
            database_errors_as_store_errors(),
            self.engine.begin() as connection,
            sign_in_entry(
                connection,
                advisory_lock_key=SIGN_IN_ENTRY_KEY,
                accounts=PostgresSubjectUserStore(self.engine, connection),
                audit=self.audit,
                sessions_table=SESSIONS,
            ) as stores,
        ):
            yield stores

    def get(self, user_id: str) -> User | None:
        """The account of that id; a read needs no entry."""
        return lobby_user(PostgresSubjectUserStore(self.engine).get(user_id))

    def delete_account(self, user_id: str) -> None:
        """Delete the account; its sessions end with it."""
        with self.engine.begin() as connection:
            connection.execute(select(func.pg_advisory_xact_lock(SIGN_IN_ENTRY_KEY)))
            connection.execute(_DELETE_ACCOUNT, {"user_id": user_id})
