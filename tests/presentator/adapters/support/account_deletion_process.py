"""One of two processes that each delete an account of one Postgres database.

Run with `python -m` and the subject to delete. It says it is ready once
connected, deletes the account when the parent lets it go, and exits 0 once the
deletion has returned.
"""

import sys

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from tests.support.postgres import engine_from_environment
from tests.support.two_processes import announce_ready_and_wait_for_go


def main() -> None:
    """Connect, wait for the go-ahead, delete the account."""
    (subject,) = sys.argv[1:]
    engine = engine_from_environment()
    writes = PostgresIdentityWrites(engine, LoggingAuditSink())
    announce_ready_and_wait_for_go()
    writes.delete_account(subject)
    engine.dispose()


if __name__ == "__main__":
    main()
