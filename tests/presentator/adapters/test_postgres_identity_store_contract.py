"""The Postgres identity stores against webauth's contract for store adapters."""

from dataclasses import dataclass

import pytest
from webauth.ports import SignInTransaction
from webauth.store_contract import STORE_CHECKS, StoreAdapter, StoreCheck

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from tests.support.postgres import engine_on


@dataclass(frozen=True, slots=True)
class _WritesOver:
    """Builds the transaction in a process that shares nothing with the test's."""

    database_url: str

    def __call__(self) -> SignInTransaction:
        return PostgresIdentityWrites(engine_on(self.database_url), LoggingAuditSink())


@pytest.mark.parametrize("check", STORE_CHECKS, ids=lambda check: check.name)
def test_the_postgres_stores_keep_the_duty_of_every_port(
    check: StoreCheck, database_url: str
) -> None:
    engine = engine_on(database_url)
    writes = _WritesOver(database_url)
    try:
        check.run(
            StoreAdapter(
                accounts=PostgresSubjectUserStore(engine),
                sessions=PostgresSessionRecordStore(engine),
                transaction=PostgresIdentityWrites(engine, LoggingAuditSink()),
                transaction_in_another_process=writes,
            )
        )
    finally:
        engine.dispose()
