"""A stop ends the chores before the instance closes and the process stops."""

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Generator
from datetime import timedelta
from functools import partial
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, TextClause, text

from presentator.adapters.postgres.locks import (
    MIGRATION_LOCK,
    hold_until_transaction_ends,
)
from presentator.host.lifecycle import ServerEvent, signals_end_as_a_stop
from presentator.host.main import Instance, served_by_host
from presentator.host.polling import ChoreCode, Schedule
from tests.presentator.application.support.fakes import PATIENCE
from tests.presentator.host.support.app_processes import (
    presentator_environment,
    serving_presentator,
    two_free_ports,
)
from tests.support.lobby import a_settings
from tests.support.log_lines import (
    events_of,
    json_lines,
    json_lines_among,
    json_logged,
)
from tests.support.postgres import engine_on
from tests.support.two_processes import CONSOLE_ENTRY_POINT

_STATEMENT_TIMEOUT_SECONDS = 1
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)
# A shell's and a container runtime's exit status of a process a signal ended.
_SIGNALLED_EXIT_BASE = 128
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
        events.index((ServerEvent.PROCESS_STOPPED, None))
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
    assert events.count(ServerEvent.PROCESS_STOPPED) == 1
    assert "process.crashed" not in events


def _lock_the_schema_version(holder: Connection) -> None:
    holder.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))


def _lock_the_pins(holder: Connection) -> None:
    holder.execute(text("LOCK TABLE host_key_pins IN ACCESS EXCLUSIVE MODE"))


@pytest.mark.parametrize(
    ("arguments", "hold"),
    [
        pytest.param(
            [],
            _lock_the_schema_version,
            id="the start, checking the schema",
        ),
        pytest.param(
            ["migrate"],
            partial(hold_until_transaction_ends, lock=MIGRATION_LOCK),
            id="migrate, waiting for another migration",
        ),
        pytest.param(
            ["unpin-host", "git.example"],
            _lock_the_pins,
            id="unpin-host, waiting for the pins",
        ),
    ],
)
@pytest.mark.usefixtures("environment")
def test_a_sigint_before_any_serving_ends_the_process_in_its_stop_line_alone(
    database_url: str,
    tmp_path: Path,
    arguments: list[str],
    hold: Callable[[Connection], None],
) -> None:
    engine = engine_on(database_url)
    port, _ = two_free_ports()
    try:
        with engine.connect() as holder:
            hold(holder)
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
    assert events[-1] == ServerEvent.PROCESS_STOPPED
    assert events.count(ServerEvent.PROCESS_STOPPED) == 1
    assert ServerEvent.PROCESS_FAILED not in events
    assert "process.crashed" not in events
    assert errors == ""


@pytest.fixture
def raised_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[list[tuple[int, object]]]:
    """The signals raised, each with its handler then; recorded, not done.

    The signal would end the test run.
    """
    raised: list[tuple[int, object]] = []

    def recorded(signal_number: int) -> None:
        raised.append((signal_number, signal.getsignal(signal_number)))

    monkeypatch.setattr(signal, "raise_signal", recorded)
    handlers = {number: signal.getsignal(number) for number in _STOP_SIGNALS}
    try:
        yield raised
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)


def _interrupted_by(stop_signal: signal.Signals) -> None:
    """Deliver `stop_signal` to this process, as a shell or a container runtime does."""
    # os.kill runs the handler of a signal it sends to this process before it returns.
    os.kill(os.getpid(), stop_signal)


@pytest.mark.parametrize("stop_signal", _STOP_SIGNALS)
def test_a_stop_signal_ends_the_process_by_itself_at_its_default_action(
    raised_signals: list[tuple[int, object]], stop_signal: signal.Signals
) -> None:
    with pytest.raises(SystemExit), signals_end_as_a_stop():
        _interrupted_by(stop_signal)

    assert raised_signals == [(stop_signal, signal.SIG_DFL)]


@pytest.mark.parametrize("stop_signal", _STOP_SIGNALS)
@pytest.mark.usefixtures("raised_signals")
def test_a_stop_signal_its_default_action_ignores_ends_in_its_exit_status(
    stop_signal: signal.Signals,
) -> None:
    # The recorded signal is not done, as the kernel ignores it for a PID 1.
    with pytest.raises(SystemExit) as ended, signals_end_as_a_stop():
        _interrupted_by(stop_signal)

    assert ended.value.code == _SIGNALLED_EXIT_BASE + stop_signal


def _stopped_by_an_interrupt(*, server_wrote_its_line: bool) -> None:
    with signals_end_as_a_stop() as stop_line:
        if server_wrote_its_line:
            stop_line.write()
        raise KeyboardInterrupt


@pytest.mark.parametrize(
    "server_wrote_its_line",
    [pytest.param(False, id="before serving"), pytest.param(True, id="after serving")],
)
@pytest.mark.usefixtures("raised_signals")
def test_an_interrupt_ends_in_one_stop_line_wherever_it_lands(
    capsys: pytest.CaptureFixture[str], *, server_wrote_its_line: bool
) -> None:
    with json_logged(capsys) as logged, pytest.raises(SystemExit):
        _stopped_by_an_interrupt(server_wrote_its_line=server_wrote_its_line)

    assert len(events_of(logged, ServerEvent.PROCESS_STOPPED)) == 1


@pytest.mark.parametrize("stop_signal", _STOP_SIGNALS)
@pytest.mark.usefixtures("raised_signals")
def test_a_stop_signal_the_parent_ignores_stays_ignored_in_the_block(
    stop_signal: signal.Signals,
) -> None:
    signal.signal(stop_signal, signal.SIG_IGN)

    with signals_end_as_a_stop():
        handler_in_the_block = signal.getsignal(stop_signal)

    assert handler_in_the_block == signal.SIG_IGN


@pytest.mark.parametrize("stop_signal", _STOP_SIGNALS)
def test_the_stop_signals_get_their_handlers_back_after_the_block(
    stop_signal: signal.Signals,
) -> None:
    handler = signal.getsignal(stop_signal)

    with signals_end_as_a_stop():
        pass

    assert signal.getsignal(stop_signal) == handler
