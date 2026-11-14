"""The SQLite identity stores against webauth's contract for store adapters."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from webauth.ports import SignInTransaction
from webauth.store_contract import STORE_CHECKS, StoreAdapter, StoreCheck

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.identity import (
    ProcessWriteLock,
    SqliteIdentityWrites,
    SqliteSessionRecordStore,
    SqliteSubjectUserStore,
    create_identity_tables,
)


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
            accounts=SqliteSubjectUserStore(database),
            sessions=SqliteSessionRecordStore(database),
            transaction=writes(),
            transaction_in_another_process=writes,
        )
    )
