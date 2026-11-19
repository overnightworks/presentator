"""Every command names one store fault by the same reason, in one failed line."""

from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from presentator.adapters.postgres.locks import LockClass, lock_of, parameters
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.host.config import load_settings
from tests.presentator.host.support.app_processes import Ended, ended_presentator
from tests.support.log_lines import json_lines
from tests.support.postgres import NO_DATABASE_HERE, engine_on, point_at

_START: tuple[str, ...] = ()
_MIGRATE = ("migrate",)
_UNPIN_HOST = ("unpin-host", "git.example")
# A failed process ends with this status, whichever command it ran.
_FAILED = 1
# Short enough that a test waiting behind a lock ends at once.
_SHORT_BOUNDS = {
    "PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS": "0.5",
    "PRESENTATOR_BUILD_TIMEOUT_SECONDS": "0.5",
}


def _failed_line(ended: Ended) -> Mapping[str, object]:
    """The one `process.failed` line of a run that wrote nothing but JSON lines."""
    assert ended.exit_code == _FAILED
    assert not ended.errors
    (failed,) = [
        line for line in json_lines(ended.output) if line["event"] == "process.failed"
    ]
    assert failed["level"] == "error"
    return failed


def _an_absent_role(database_url: str) -> str:
    absent = make_url(database_url).set(username="presentator_absent_role")
    return absent.render_as_string()


def _nowhere(_: str) -> str:
    return NO_DATABASE_HERE


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize(
    "arguments", [_START, _MIGRATE, _UNPIN_HOST], ids=["start", "migrate", "unpin-host"]
)
@pytest.mark.parametrize(
    ("reached_at", "reason"),
    [(_an_absent_role, "login_refused"), (_nowhere, "not_answering")],
    ids=["login-refused", "not-answering"],
)
def test_a_store_that_cannot_be_reached_reads_the_same_reason_at_every_command(
    arguments: tuple[str, ...],
    reached_at: Callable[[str], str],
    reason: str,
    database_url: str,
    tmp_path: Path,
) -> None:
    ended = ended_presentator(arguments, reached_at(database_url), tmp_path)

    failed = _failed_line(ended)
    assert (failed["reason"], failed["error_type"]) == (
        reason,
        psycopg.OperationalError.__name__,
    )
    assert "127.0.0.1" not in ended.output


@contextmanager
def _the_toolchain_presence_held(
    database_url: str, environment: pytest.MonkeyPatch, directory: Path
) -> Generator[None]:
    point_at(environment, database_url, directory)
    toolchain = toolchain_identity_of(load_settings().toolchain)
    presence = parameters(lock_of(LockClass.TOOLCHAIN_PRESENCE, toolchain.digest))
    engine = engine_on(database_url)
    try:
        with engine.connect() as another_process:
            another_process.execute(
                text("SELECT pg_advisory_lock(:lock_class, :instance)"), presence
            )
            yield
    finally:
        engine.dispose()


@contextmanager
def _the_host_key_pins_locked(
    database_url: str, _environment: pytest.MonkeyPatch, _directory: Path
) -> Generator[None]:
    engine = engine_on(database_url)
    try:
        with engine.connect() as another_process:
            another_process.execute(
                text("LOCK TABLE host_key_pins IN ACCESS EXCLUSIVE MODE")
            )
            yield
            another_process.rollback()
    finally:
        engine.dispose()


type Locked = Callable[[str, pytest.MonkeyPatch, Path], AbstractContextManager[None]]


@dataclass(frozen=True, slots=True)
class _Cancelled:
    """A command, the lock it waits behind, and the error the server cancels with."""

    arguments: tuple[str, ...]
    locked: Locked
    driver_error: type[psycopg.Error]


@pytest.mark.parametrize(
    "cancelled",
    [
        _Cancelled(
            _START, _the_toolchain_presence_held, psycopg.errors.LockNotAvailable
        ),
        _Cancelled(
            _UNPIN_HOST, _the_host_key_pins_locked, psycopg.errors.QueryCanceled
        ),
    ],
    ids=["start", "unpin-host"],
)
def test_a_statement_the_server_cancels_behind_a_lock_reads_statement_cancelled(
    cancelled: _Cancelled,
    environment: pytest.MonkeyPatch,
    database_url: str,
    tmp_path: Path,
) -> None:
    with cancelled.locked(database_url, environment, tmp_path):
        ended = ended_presentator(
            cancelled.arguments, database_url, tmp_path, _SHORT_BOUNDS
        )

    failed = _failed_line(ended)
    assert (failed["reason"], failed["error_type"]) == (
        "statement_cancelled",
        cancelled.driver_error.__name__,
    )
    assert "SELECT" not in ended.output
