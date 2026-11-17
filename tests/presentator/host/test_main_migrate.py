"""`presentator migrate` brings the database to head, alone or twice at once."""

import time
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from presentator.adapters.postgres.locks import MIGRATION_LOCK
from presentator.host import main
from tests.support.log_lines import json_logged
from tests.support.postgres import (
    NO_DATABASE_HERE,
    database_environment,
    engine_on,
    point_at,
)
from tests.support.two_processes import two_presentators

_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS = "9999"
_WAITING_FOR_A_LOCK = """
SELECT count(*) FROM pg_locks
WHERE locktype = 'advisory' AND NOT granted
  AND database = (SELECT oid FROM pg_database WHERE datname = current_database())
"""
_BOTH_MIGRATIONS = 2
_WAIT_SECONDS = 30
_POLL_SECONDS = 0.05
_REVISIONS = text("SELECT version_num FROM alembic_version")


def _revisions(engine: Engine) -> list[str]:
    with engine.connect() as connection:
        return [revision for (revision,) in connection.execute(_REVISIONS)]


def test_migrate_brings_an_empty_database_to_head(
    unmigrated_database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, unmigrated_database_url, tmp_path)

    main.main(["migrate"])

    engine = engine_on(unmigrated_database_url)
    try:
        assert "decks" in inspect(engine).get_table_names()
        assert len(_revisions(engine)) == 1
    finally:
        engine.dispose()


def test_migrate_writes_no_free_text_line_of_alembic(
    unmigrated_database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    point_at(monkeypatch, unmigrated_database_url, tmp_path)

    with json_logged(capsys) as logged:
        main.main(["migrate"])

    assert not [
        line for line in logged.lines if str(line["logger"]).startswith("alembic")
    ]


def test_a_migrate_against_a_database_that_does_not_answer_ends_in_one_failed_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    point_at(monkeypatch, NO_DATABASE_HERE, tmp_path)

    with json_logged(capsys) as logged, pytest.raises(SystemExit) as ended:
        main.main(["migrate"])

    assert ended.value.code == 1
    (failed,) = logged.lines
    assert (failed["event"], failed["level"]) == ("process.failed", "error")
    assert (failed["reason"], failed["error_type"]) == (
        "not_answering",
        psycopg.OperationalError.__name__,
    )
    assert "127.0.0.1" not in logged.text


def test_two_concurrent_migrations_wait_for_one_another_and_both_end_at_head(
    unmigrated_database_url: str, tmp_path: Path
) -> None:
    environment = database_environment(unmigrated_database_url, tmp_path)
    engine = engine_on(unmigrated_database_url)
    try:
        with engine.connect() as holder:
            lock = {
                "lock_class": int(MIGRATION_LOCK.lock_class),
                "instance": MIGRATION_LOCK.instance,
            }
            holder.execute(
                text("SELECT pg_advisory_lock(:lock_class, :instance)"), lock
            )
            with two_presentators(["migrate"], environment) as processes:
                _wait_until_both_wait_for_the_lock(engine)
                holder.execute(
                    text("SELECT pg_advisory_unlock(:lock_class, :instance)"), lock
                )
                finished = processes.finish()

        assert [each.exit_code for each in finished] == [0, 0], finished
        assert len(_revisions(engine)) == 1
    finally:
        engine.dispose()


def test_two_migrations_of_a_database_a_newer_release_made_exit_zero_and_leave_it(
    unmigrated_database_url: str, tmp_path: Path
) -> None:
    environment = database_environment(unmigrated_database_url, tmp_path)
    engine = engine_on(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num varchar(32) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"),
                {"revision": _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS},
            )

        with two_presentators(["migrate"], environment) as processes:
            finished = processes.finish()

        assert [each.exit_code for each in finished] == [0, 0], finished
        assert _revisions(engine) == [_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS]
        assert inspect(engine).get_table_names() == ["alembic_version"]
    finally:
        engine.dispose()


def _wait_until_both_wait_for_the_lock(engine: Engine) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            if (
                connection.execute(text(_WAITING_FOR_A_LOCK)).scalar_one()
                == _BOTH_MIGRATIONS
            ):
                return
        time.sleep(_POLL_SECONDS)
    pytest.fail("the two migrations never both waited for the lock")
