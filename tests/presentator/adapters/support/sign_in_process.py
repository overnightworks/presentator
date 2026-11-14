"""One of two processes that sign the same subjects in against one Postgres database.

Run as a script. It prints READY once connected, signs in after a line on stdin,
and exits 0 only when every sign-in went through.
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from webauth.ports import AccountKind, SignInTransaction

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from presentator.contracts.models import Role

READY: Final = "ready"
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
    password = Path(os.environ["PRESENTATOR_DATABASE_PASSWORD_FILE"]).read_text(
        encoding="utf-8"
    )
    engine = create_database_engine(
        os.environ["PRESENTATOR_DATABASE_URL"], SecretStr(password)
    )
    transaction = PostgresIdentityWrites(engine, LoggingAuditSink())
    print(READY, flush=True)  # noqa: T201 - the test reads this line
    sys.stdin.readline()
    for subject in SUBJECTS:
        sign_in(transaction, subject, f"{subject}-by-{os.getpid()}")
    engine.dispose()


if __name__ == "__main__":
    main()
