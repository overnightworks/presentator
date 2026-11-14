"""Two processes signing the same subjects in at once leave one account each."""

import sys
from pathlib import Path

from sqlalchemy import text

from tests.presentator.adapters.support.sign_in_process import READY, SUBJECTS
from tests.support.postgres import database_environment, engine_on
from tests.support.two_processes import two_processes

_CHILD = Path(__file__).parent / "support" / "sign_in_process.py"


def test_two_processes_signing_the_same_subjects_in_at_once_leave_one_account_each(
    database_url: str, tmp_path: Path
) -> None:
    environment = database_environment(database_url, tmp_path)

    with two_processes([sys.executable, str(_CHILD)], environment) as processes:
        for each in (processes.first, processes.second):
            assert each.stdout is not None
            assert each.stdout.readline().strip() == READY
        for each in (processes.first, processes.second):
            assert each.stdin is not None
            each.stdin.write("go\n")
            each.stdin.flush()
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
    assert sorted(accounts) == sorted((subject, 1) for subject in SUBJECTS)
    assert sorted(sessions) == sorted((subject, 2) for subject in SUBJECTS)
