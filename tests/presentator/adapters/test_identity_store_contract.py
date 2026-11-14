"""The SQLite identity stores against webauth's contract for store adapters."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from webauth.ports import AccountKind, SignInTransaction
from webauth.store_contract import STORE_CHECKS, StoreAdapter, StoreCheck

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SqliteSubjectUserStore,
    StoredAccount,
    create_identity_tables,
)
from presentator.contracts.models import Role

_ROLE_THE_KIT_WRITES = "member"


@dataclass(frozen=True, slots=True)
class _AccountsStoringTheKitsRole(SqliteSubjectUserStore):
    """The accounts, with the kit's role word read as the one role a member has here.

    The kit creates its accounts with the role "member", which this product's
    closed `Role` refuses; webauth itself only ever writes "admin" and "user".
    """

    def create_for_subject(
        self, subject: str, username: str, role: str, kind: AccountKind
    ) -> StoredAccount:
        stored_role = Role.USER.value if role == _ROLE_THE_KIT_WRITES else role
        return super().create_for_subject(subject, username, stored_role, kind)


@dataclass(frozen=True, slots=True)
class _WritesOver:
    database: Path

    def __call__(self) -> SignInTransaction:
        return SqliteIdentityWrites(
            self.database, ProcessWriteLock(), LoggingAuditSink()
        )


@pytest.mark.parametrize("check", STORE_CHECKS, ids=lambda check: check.name)
def test_the_sqlite_stores_keep_the_duty_of_every_port(
    check: StoreCheck, tmp_path: Path
) -> None:
    database = tmp_path / "identity.sqlite3"
    create_identity_tables(database)
    writes = _WritesOver(database)

    check.run(
        StoreAdapter(
            accounts=_AccountsStoringTheKitsRole(database),
            sessions=SqliteSessionRecordStore(database),
            transaction=writes(),
            transaction_in_another_process=writes,
        )
    )
