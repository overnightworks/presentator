"""A stop ends the chores before the instance closes and the process stops."""

import signal
import subprocess
import threading
import time
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, TextClause, text

from presentator.adapters.postgres.locks import MIGRATION_LOCK, parameters
from presentator.host.main import Instance, served_by_host
from presentator.host.polling import ChoreCode, Schedule
from presentator.host.stopping_server import interrupt_ends_as_a_stop
from tests.presentator.application.support.fakes import PATIENCE
from tests.presentator.host.support.app_processes import (
    CONSOLE_ENTRY_POINT,
    presentator_environment,
    serving_presentator,
    two_free_ports,
)
from tests.support.lobby import a_settings
from tests.support.log_lines import json_lines, json_lines_among
from tests.support.postgres import engine_on

_STATEMENT_TIMEOUT_SECONDS = 1
# What the stop takes besides the poll's wait: the server's own stop, the
# stores' close and the end of the interpreter.
_STOP_MARGIN_SECONDS = 3
_POLL_SECONDS = 0.05
_A_POLL_WAITS_FOR_THE_LOCK = text(
    "SELECT count(*) FROM pg_locks WHERE relation = 'sources'::regclass AND NOT granted"
)
_A_STATEMENT_WAITS_FOR_A_LOCK = text(
    "SELECT count(*) FROM pg_locks WHERE NOT granted"
    " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
)
# Longer than the wait for the lock, so the interrupt and not the timeout ends it.
_LONG_STATEMENT_SECONDS = 60


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


def _wait_until_a_statement_waits(engine: Engine, waiting: TextClause) -> None:
    deadline = time.monotonic() + PATIENCE.total_seconds()
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            if connection.execute(waiting).scalar_one():
                return
        time.sleep(_POLL_SECONDS)
    pytest.fail("no statement ever waited for the lock")


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
            _wait_until_a_statement_waits(engine, _A_POLL_WAITS_FOR_THE_LOCK)
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


@pytest.mark.usefixtures("environment")
def test_a_sigint_ends_a_served_process_in_its_stop_line_alone(
    database_url: str, tmp_path: Path
) -> None:
    with serving_presentator(database_url, tmp_path, {}) as app:
        app.process.send_signal(signal.SIGINT)
        exit_code = app.process.wait(timeout=PATIENCE.total_seconds())
        output = app.output_after_exit()

    events = [line["event"] for line in json_lines(output)]
    assert exit_code == -signal.SIGINT
    assert events.count("process.stopped") == 1
    assert "process.crashed" not in events


@pytest.mark.parametrize(
    ("arguments", "held", "held_with"),
    [
        pytest.param(
            [],
            text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"),
            {},
            id="the start, checking the schema",
        ),
        pytest.param(
            ["migrate"],
            text("SELECT pg_advisory_xact_lock(:lock_class, :instance)"),
            parameters(MIGRATION_LOCK),
            id="migrate, waiting for another migration",
        ),
        pytest.param(
            ["unpin-host", "git.example"],
            text("LOCK TABLE host_key_pins IN ACCESS EXCLUSIVE MODE"),
            {},
            id="unpin-host, waiting for the pins",
        ),
    ],
)
@pytest.mark.usefixtures("environment")
def test_a_sigint_before_any_serving_ends_the_process_in_its_stop_line_alone(
    database_url: str,
    tmp_path: Path,
    arguments: list[str],
    held: TextClause,
    held_with: Mapping[str, int],
) -> None:
    engine = engine_on(database_url)
    port, _ = two_free_ports()
    try:
        with engine.connect() as holder:
            holder.execute(held, held_with)
            with subprocess.Popen(  # noqa: S603 - the command is this repository's own
                [str(CONSOLE_ENTRY_POINT), *arguments],
                env=presentator_environment(
                    database_url,
                    tmp_path,
                    port,
                    {
                        "PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS": str(
                            _LONG_STATEMENT_SECONDS
                        )
                    },
                ),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as process:
                try:
                    _wait_until_a_statement_waits(engine, _A_STATEMENT_WAITS_FOR_A_LOCK)
                    process.send_signal(signal.SIGINT)
                    output, errors = process.communicate(
                        timeout=PATIENCE.total_seconds()
                    )
                finally:
                    process.kill()
            holder.rollback()
    finally:
        engine.dispose()

    events = [line["event"] for line in json_lines(output)]
    # The default action of SIGINT, which a shell or a container runtime reports as 130.
    assert process.returncode == -signal.SIGINT
    assert events[-1] == "process.stopped"
    assert events.count("process.stopped") == 1
    assert "process.failed" not in events
    assert "process.crashed" not in events
    assert errors == ""


def test_an_interrupt_ends_the_process_by_sigint_at_its_default_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The raise is recorded, not done: the signal would end the test run.
    raised: list[tuple[int, object]] = []

    def recorded(signal_number: int) -> None:
        raised.append((signal_number, signal.getsignal(signal.SIGINT)))

    monkeypatch.setattr(signal, "raise_signal", recorded)
    handler = signal.getsignal(signal.SIGINT)
    try:
        with interrupt_ends_as_a_stop():
            raise KeyboardInterrupt
    finally:
        signal.signal(signal.SIGINT, handler)

    assert raised == [(signal.SIGINT, signal.SIG_DFL)]
