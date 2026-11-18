"""The refresh owner on Postgres: one advisory lock, one epoch, one presence key.

The advisory lock tells the live holder from a dead one, since it lasts as long
as the session that holds it. A session that lost its connection could still
write, so every write of the holder also reads the epoch in `refresh_owner`
`FOR SHARE` and is refused when another holder has bumped it. A new holder's
bump waits for those open transactions, and it writes nothing before the bump.
The one pinned connection holds the lock and the toolchain's presence key, so
both end with the process. The in-process lock around every call keeps the
pinned connection to one user at a time.
"""

import logging
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager, suppress
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final, Protocol

from psycopg.errors import LockNotAvailable
from sqlalchemy import Connection, CursorResult, Engine, TextClause, text
from sqlalchemy.exc import DBAPIError

from presentator.adapters.postgres.engine import milliseconds_of
from presentator.adapters.postgres.locks import (
    REFRESH_LOCK,
    LockClass,
    lock_of,
    parameters,
)
from presentator.contracts.decks import ToolchainIdentity
from presentator.ports.decks import (
    RefreshOwnershipLostError,
    RefreshOwnershipTimedOutError,
)

_TRY_LOCK: Final = text("SELECT pg_try_advisory_lock(:lock_class, :instance)")
_WAIT_FOR_LOCK: Final = text("SELECT pg_advisory_lock(:lock_class, :instance)")
_UNLOCK: Final = text("SELECT pg_advisory_unlock(:lock_class, :instance)")
_SHARE_LOCK: Final = text("SELECT pg_advisory_lock_shared(:lock_class, :instance)")
# Every wait on the pinned connection ends within the bound: the lock a removal
# waits for, the bump that waits for a holder's open transaction, the presence key.
# Its statement timeout leaves room for that wait on top of the statement's own
# run, so a wait ends as the lock timeout it is and not as a cancelled statement.
_BOUND_WAITS: Final = text(
    "SELECT set_config('lock_timeout', :lock_milliseconds, false),"
    " set_config('statement_timeout', :statement_milliseconds, false)"
)
_BUMP: Final = text(
    "UPDATE refresh_owner SET epoch = epoch + 1 WHERE id = 1 RETURNING epoch"
)
_READ_EPOCH: Final = text("SELECT epoch FROM refresh_owner WHERE id = 1 FOR SHARE")
_PING: Final = text("SELECT 1")
_LET_GO: Final = "refresh_lock.let_go"

_log = logging.getLogger(__name__)


class LetGoReason(StrEnum):
    """Why the refresh lock was let go: the closed set of `reason` codes."""

    BUMP_TIMED_OUT = "bump_timed_out"
    CONNECTION_GONE = "connection_gone"


class RefreshFence(Protocol):
    """What a write of the holder asks inside its own transaction."""

    def check(self, connection: Connection) -> None:
        """Refuse with `RefreshOwnershipLostError` unless the holder still holds."""


def fenced_write(
    engine: Engine,
    fence: RefreshFence,
    statement: TextClause,
    arguments: Mapping[str, object],
) -> int:
    """Run one write of the holder in a transaction of its own; the rows it touched."""
    with engine.begin() as connection:
        fence.check(connection)
        return connection.execute(statement, arguments).rowcount


def _waited_too_long(failure: DBAPIError) -> bool:
    return isinstance(failure.orig, LockNotAvailable)


class PostgresRefreshOwnership:
    """Holds the refresh, and the presence of one toolchain, on a pinned connection."""

    def __init__(
        self,
        engine: Engine,
        *,
        toolchain: ToolchainIdentity,
        lock_timeout: timedelta,
        statement_timeout: timedelta,
    ) -> None:
        """Pin a connection and announce the toolchain on it.

        A statement there may wait `lock_timeout`, then run `statement_timeout`.
        """
        self._engine = engine
        self._presence = lock_of(LockClass.TOOLCHAIN_PRESENCE, toolchain.digest)
        self._bounds = {
            "lock_milliseconds": str(milliseconds_of(lock_timeout)),
            "statement_milliseconds": str(
                milliseconds_of(lock_timeout + statement_timeout)
            ),
        }
        self._pinned: Connection | None = None
        self._epoch: int | None = None
        self._pinned_connection()

    def try_hold(self) -> AbstractContextManager[bool]:
        """Hold the refresh while the block runs, or answer False at once."""
        return self._holding(self._take_if_free)

    @contextmanager
    def hold(self) -> Generator[None]:
        """Wait for the refresh, then hold it while the block runs."""
        with self._holding(self._take_waiting) as held:
            if not held:
                raise RefreshOwnershipTimedOutError
            yield

    def recover(self) -> None:
        """Check the pinned connection; a dead one is replaced and announces again.

        A failed check has already ended its connection, so only the reconnect remains.
        """
        with suppress(DBAPIError):
            self._run(_PING)
        self._pinned_connection()

    @contextmanager
    def fenced(self) -> Generator[None]:
        """Run the block in one transaction that reads the epoch first."""
        with self._engine.begin() as connection:
            self.check(connection)
            yield

    def check(self, connection: Connection) -> None:
        """Share the epoch row in this transaction and refuse when it is not ours."""
        if connection.execute(_READ_EPOCH).scalar_one() != self._epoch:
            raise RefreshOwnershipLostError

    def toolchain_is_present(self, toolchain: ToolchainIdentity) -> bool:
        """Whether some session holds that toolchain's presence key."""
        key = parameters(lock_of(LockClass.TOOLCHAIN_PRESENCE, toolchain.digest))
        if self._run(_TRY_LOCK, key).scalar_one():
            self._run(_UNLOCK, key)
            return False
        return True

    def close(self) -> None:
        """Let go of the pinned connection, and with it every lock it holds."""
        self._epoch = None
        self._discard()

    @contextmanager
    def _holding(self, take: Callable[[], bool]) -> Generator[bool]:
        held = self._become_holder(take)
        try:
            yield held
        finally:
            if held:
                self._release()

    def _take_if_free(self) -> bool:
        return bool(self._run(_TRY_LOCK, parameters(REFRESH_LOCK)).scalar_one())

    def _take_waiting(self) -> bool:
        self._run(_WAIT_FOR_LOCK, parameters(REFRESH_LOCK))
        return True

    def _become_holder(self, take: Callable[[], bool]) -> bool:
        """Take the lock, then the epoch; False where either is not free in time."""
        try:
            if not take():
                return False
        except DBAPIError as failure:
            if _waited_too_long(failure):
                return False
            raise
        try:
            self._epoch = self._run(_BUMP).scalar_one()
        except DBAPIError as failure:
            self._release()
            if not _waited_too_long(failure):
                raise
            _log.warning(_LET_GO, extra={"reason": LetGoReason.BUMP_TIMED_OUT})
            return False
        return True

    def _release(self) -> None:
        self._epoch = None
        if self._pinned is None:
            return
        try:
            self._run(_UNLOCK, parameters(REFRESH_LOCK))
        except DBAPIError:
            _log.warning(_LET_GO, extra={"reason": LetGoReason.CONNECTION_GONE})

    def _run(
        self, statement: TextClause, arguments: Mapping[str, int] | None = None
    ) -> CursorResult[Any]:
        connection = self._pinned_connection()
        try:
            return connection.execute(statement, arguments or {})
        except DBAPIError as failure:
            if failure.connection_invalidated:
                self._discard()
            raise

    def _pinned_connection(self) -> Connection:
        if self._pinned is None:
            connection = self._engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            )
            try:
                connection.execute(_BOUND_WAITS, self._bounds)
                connection.execute(_SHARE_LOCK, parameters(self._presence))
            except BaseException:
                connection.invalidate()
                connection.close()
                raise
            self._pinned = connection
        return self._pinned

    def _discard(self) -> None:
        """End the pinned connection for good, never returning it to the pool."""
        pinned, self._pinned = self._pinned, None
        if pinned is not None:
            pinned.invalidate()
            pinned.close()
