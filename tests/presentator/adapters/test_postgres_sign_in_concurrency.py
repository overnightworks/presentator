"""Two processes signing the same subjects in at once leave one account each."""

import time
from pathlib import Path

from sqlalchemy import Engine, text
from webauth.ports import AccountEventKind, AccountKind

from presentator.adapters.audit import LoggingAuditSink
from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.sign_in import PostgresIdentityWrites
from tests.presentator.adapters.support.sign_in_process import SUBJECTS
from tests.support.postgres import (
    database_environment,
    engine_on,
    waiting_advisory_locks,
)
from tests.support.two_processes import release_together, two_modules

_CHILD = "tests.presentator.adapters.support.sign_in_process"
_DELETING_CHILD = "tests.presentator.adapters.support.account_deletion_process"
_LEAVERS = ("leaver-1", "leaver-2")
_PATIENCE_SECONDS = 30
_POLL_SECONDS = 0.05


def test_two_processes_signing_subjects_in_at_once_create_and_audit_one_account_each(
    database_url: str, tmp_path: Path
) -> None:
    environment = database_environment(database_url, tmp_path)
    events_files = [tmp_path / "first-events", tmp_path / "second-events"]

    with two_modules(
        _CHILD,
        [str(events_files[0])],
        environment,
        second_arguments=[str(events_files[1])],
    ) as processes:
        release_together(processes)
        finished = processes.finish()

    engine = engine_on(database_url)
    try:
        with engine.connect() as connection:
            accounts = connection.execute(
                text("SELECT id, COUNT(*) FROM users GROUP BY id")
            ).all()
            sessions = connection.execute(
                text("SELECT account_id, COUNT(*) FROM sessions GROUP BY account_id")
            ).all()
    finally:
        engine.dispose()
    assert [each.exit_code for each in finished] == [0, 0], finished
    events = [
        line.split()
        for each in events_files
        for line in each.read_text(encoding="utf-8").splitlines()
    ]
    assert sorted(accounts) == sorted((subject, 1) for subject in SUBJECTS)
    assert sorted(sessions) == sorted((subject, 2) for subject in SUBJECTS)
    assert sorted(events) == sorted(
        [AccountEventKind.USER_CREATED.value, subject] for subject in SUBJECTS
    )


def test_deleting_accounts_in_two_processes_waits_for_the_open_sign_in_entry(
    database_url: str, tmp_path: Path
) -> None:
    engine = engine_on(database_url)
    accounts = PostgresSubjectUserStore(engine)
    for subject in _LEAVERS:
        accounts.create_for_subject(subject, subject, "user", AccountKind.PERSON)
    environment = database_environment(database_url, tmp_path)
    writes = PostgresIdentityWrites(engine, LoggingAuditSink())

    try:
        with two_modules(
            _DELETING_CHILD,
            [_LEAVERS[0]],
            environment,
            second_arguments=[_LEAVERS[1]],
        ) as processes:
            with writes():
                release_together(processes)
                _until_both_wait(engine)
                still_running = [processes.first.poll(), processes.second.poll()]
                accounts_during_entry = [accounts.get(each) for each in _LEAVERS]
            finished = processes.finish()
        accounts_after = [accounts.get(each) for each in _LEAVERS]
    finally:
        engine.dispose()

    assert still_running == [None, None]
    assert None not in accounts_during_entry
    assert [each.exit_code for each in finished] == [0, 0], finished
    assert accounts_after == [None, None]


def _until_both_wait(engine: Engine) -> None:
    deadline = time.monotonic() + _PATIENCE_SECONDS
    while waiting_advisory_locks(engine) < len(_LEAVERS):
        assert time.monotonic() < deadline, "no deletion waits for the sign-in lock"
        time.sleep(_POLL_SECONDS)
