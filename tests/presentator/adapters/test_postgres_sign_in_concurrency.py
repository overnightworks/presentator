"""Two processes signing the same subjects in at once leave one account each."""

from pathlib import Path

from sqlalchemy import text
from webauth.ports import AccountEventKind

from tests.presentator.adapters.support.sign_in_process import SUBJECTS
from tests.support.postgres import database_environment, engine_on
from tests.support.two_processes import release_together, two_modules

_CHILD = "tests.presentator.adapters.support.sign_in_process"


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
                text("SELECT user_id, COUNT(*) FROM sessions GROUP BY user_id")
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
