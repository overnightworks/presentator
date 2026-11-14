"""The sign-in transaction on Postgres, behind webauth's `SignInTransaction`."""

from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, text
from webauth.ports import AuditSink, SignInStores

from presentator.adapters.audit import BufferedAudit
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.locks import (
    SIGN_IN_LOCK,
    hold_until_transaction_ends,
)
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.contracts.models import User

_DELETE_SESSIONS_OF: Final = text("DELETE FROM sessions WHERE user_id = :user_id")
_DELETE_ACCOUNT: Final = text("DELETE FROM users WHERE id = :user_id")


@dataclass(frozen=True, slots=True)
class PostgresIdentityWrites:
    """Every identity write, each one atomic, serialized across processes.

    Calling it enters webauth's `SignInTransaction`: one transaction over accounts
    and sessions that first takes the sign-in advisory lock, so entries of any
    number of processes run one after another, and that rolls back when the block
    raises. The audit events of the entry reach the sink only after COMMIT, so the
    log names nothing that was rolled back. Deleting an account takes the same lock
    and transaction.
    """

    engine: Engine
    audit: AuditSink

    def __call__(self) -> AbstractContextManager[SignInStores]:
        """Enter one sign-in transaction; its stores share the entry's connection."""
        return self._entry()

    @contextmanager
    def _entry(self) -> Generator[SignInStores]:
        buffered = BufferedAudit()
        with self.engine.begin() as connection:
            hold_until_transaction_ends(connection, SIGN_IN_LOCK)
            yield SignInStores(
                accounts=PostgresSubjectUserStore(self.engine, connection),
                sessions=PostgresSessionRecordStore(self.engine, connection),
                audit=buffered,
            )
        buffered.flush_to(self.audit)

    def get(self, user_id: str) -> User | None:
        """The account of that id; a read needs no entry."""
        account = PostgresSubjectUserStore(self.engine).get(user_id)
        if account is None:
            return None
        return User(id=account.id, username=account.username, role=account.role)

    def delete_account(self, user_id: str) -> None:
        """Delete the account and its sessions together."""
        with self.engine.begin() as connection:
            hold_until_transaction_ends(connection, SIGN_IN_LOCK)
            connection.execute(_DELETE_SESSIONS_OF, {"user_id": user_id})
            connection.execute(_DELETE_ACCOUNT, {"user_id": user_id})
