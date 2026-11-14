"""One of two processes that sign the same subjects in against one Postgres database.

Run with `python -m`. It says it is ready once connected, signs in when the parent
lets it go, and exits 0 only when every sign-in went through.
"""

import os
from datetime import UTC, datetime, timedelta
from typing import Final

from webauth.ports import AccountKind, SignInTransaction

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role
from tests.support.postgres import engine_from_environment
from tests.support.two_processes import announce_ready_and_wait_for_go

SUBJECTS: Final = tuple(f"concurrent-subject-{number}" for number in range(25))
_SESSION_LIFETIME: Final = timedelta(hours=1)


def sign_in(transaction: SignInTransaction, subject: str, session_id: str) -> None:
    """What the callback route does: find the account or create it, then a session."""
    with transaction() as stores:
        if stores.accounts.get(subject) is None:
            stores.accounts.create_for_subject(
                subject, subject, Role.USER.value, AccountKind.PERSON
            )
        now = datetime.now(UTC)
        stores.sessions.create(
            subject,
            session_id=session_id,
            now=now,
            expires_at=now + _SESSION_LIFETIME,
            refresh_token=None,
            id_token=None,
            account_language=None,
        )


def main() -> None:
    """Connect, wait for the go-ahead, and sign every subject in."""
    engine = engine_from_environment()
    transaction = PostgresIdentityWrites(engine, LoggingAuditSink())
    announce_ready_and_wait_for_go()
    for subject in SUBJECTS:
        sign_in(transaction, subject, f"{subject}-by-{os.getpid()}")
    engine.dispose()


if __name__ == "__main__":
    main()
