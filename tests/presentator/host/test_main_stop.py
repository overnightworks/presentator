"""A stop ends the chores before the instance closes and the process stops."""

import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from presentator.host.main import Instance, served_by_host
from presentator.host.polling import ChoreCode, Schedule
from tests.presentator.application.support.fakes import PATIENCE
from tests.presentator.host.support.app_processes import serving_presentator
from tests.support.lobby import a_settings
from tests.support.log_lines import json_lines_among
from tests.support.postgres import engine_on

_STATEMENT_TIMEOUT_SECONDS = 1
# What the stop takes besides the poll's wait: the server's own stop, the
# stores' close and the end of the interpreter.
_STOP_MARGIN_SECONDS = 3
_POLL_SECONDS = 0.05
_A_POLL_WAITS_FOR_THE_LOCK = text(
    "SELECT count(*) FROM pg_locks WHERE relation = 'sources'::regclass AND NOT granted"
)


def test_leaving_the_lifespan_stops_the_chores_before_it_closes_the_instance() -> None:
    happened: list[str] = []
    ran = {chore_code: threading.Event() for chore_code in ChoreCode}

    def a_run_of(chore_code: ChoreCode) -> None:
        happened.append(chore_code)
        ran[chore_code].set()

    def a_schedule(chore_code: ChoreCode) -> Schedule:
        return Schedule(
            chore=lambda: a_run_of(chore_code),
            chore_code=chore_code,
            interval=timedelta(0),
        )

    instance = Instance(
        lobby=FastAPI(),
        talk=FastAPI(),
        poller=a_schedule(ChoreCode.SOURCE_POLL),
        session_sweep=a_schedule(ChoreCode.SESSION_SWEEP),
        close=lambda: happened.append("closed"),
    )

    with TestClient(served_by_host(instance, a_settings())):
        assert all(each.wait(PATIENCE.total_seconds()) for each in ran.values())

    assert set(happened[:-1]) == set(ChoreCode)
    assert happened[-1] == "closed"


def _wait_until_a_poll_waits_for_the_lock(engine: Engine) -> None:
    deadline = time.monotonic() + PATIENCE.total_seconds()
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            if connection.execute(_A_POLL_WAITS_FOR_THE_LOCK).scalar_one():
                return
        time.sleep(_POLL_SECONDS)
    pytest.fail("no source poll ever waited for the lock")


@pytest.mark.usefixtures("environment")
def test_a_sigterm_during_a_locked_source_poll_writes_its_failure_before_the_stop(
    database_url: str, tmp_path: Path
) -> None:
    engine = engine_on(database_url)
    try:
        with (
            serving_presentator(
                database_url,
                tmp_path,
                {
                    "PRESENTATOR_SOURCE_POLL_SECONDS": "0",
                    "PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS": str(
                        _STATEMENT_TIMEOUT_SECONDS
                    ),
                },
            ) as app,
            engine.connect() as holder,
        ):
            holder.execute(text("LOCK TABLE sources IN ACCESS EXCLUSIVE MODE"))
            _wait_until_a_poll_waits_for_the_lock(engine)
            signalled = time.monotonic()
            app.process.send_signal(signal.SIGTERM)
            exit_code = app.process.wait(timeout=PATIENCE.total_seconds())
            stopped_after = time.monotonic() - signalled
            holder.rollback()
            output = app.output_after_exit()
    finally:
        engine.dispose()

    events = [(line["event"], line.get("chore")) for line in json_lines_among(output)]
    assert exit_code == -signal.SIGTERM
    assert events.index(("scheduled_run.failed", ChoreCode.SOURCE_POLL)) < (
        events.index(("process.stopped", None))
    )
    assert stopped_after < _STATEMENT_TIMEOUT_SECONDS + _STOP_MARGIN_SECONDS
