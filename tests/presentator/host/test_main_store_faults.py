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
from presentator.host import main
from presentator.host.config import load_settings
from tests.support.log_lines import json_lines, json_logged
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


@dataclass(frozen=True, slots=True)
class _Ended:
    """The one `process.failed` line of a failed run, and all it wrote."""

    failed: Mapping[str, object]
    output: str


@dataclass(frozen=True, slots=True)
class _Commands:
    """`presentator` run in this process, on this test's environment and output."""

    environment: pytest.MonkeyPatch
    directory: Path
    capsys: pytest.CaptureFixture[str]

    def point_at(self, database_url: str) -> None:
        """Make the next command reach the database at `database_url`."""
        point_at(self.environment, database_url, self.directory)

    def ended_failed(self, arguments: tuple[str, ...]) -> _Ended:
        """Run `presentator <arguments>` to its failed end, which writes no error."""
        with json_logged(self.capsys), pytest.raises(SystemExit) as ended:
            main.main(list(arguments))
        written = self.capsys.readouterr()

        assert ended.value.code == _FAILED
        assert not written.err
        (failed,) = [
            one for one in json_lines(written.out) if one["event"] == "process.failed"
        ]
        assert failed["level"] == "error"
        return _Ended(failed, written.out)


@pytest.fixture
def commands(
    environment: pytest.MonkeyPatch,
    redis_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> _Commands:
    """The commands of an instance whose start reaches this test's Redis."""
    environment.setenv("PRESENTATOR_REDIS_URL", redis_url)
    return _Commands(environment, tmp_path, capsys)


def _an_absent_role(database_url: str) -> str:
    absent = make_url(database_url).set(username="presentator_absent_role")
    return absent.render_as_string()


def _nowhere(_: str) -> str:
    return NO_DATABASE_HERE


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
    commands: _Commands,
) -> None:
    commands.point_at(reached_at(database_url))

    ended = commands.ended_failed(arguments)

    assert (ended.failed["reason"], ended.failed["error_type"]) == (
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
    cancelled: _Cancelled, database_url: str, commands: _Commands
) -> None:
    commands.point_at(database_url)
    for name, value in _SHORT_BOUNDS.items():
        commands.environment.setenv(name, value)
    with cancelled.locked(database_url, commands.environment, commands.directory):
        ended = commands.ended_failed(cancelled.arguments)

    assert (ended.failed["reason"], ended.failed["error_type"]) == (
        "statement_cancelled",
        cancelled.driver_error.__name__,
    )
    assert "SELECT" not in ended.output
