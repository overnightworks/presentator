"""The ask lease in memory, for tests that need no Redis."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)


@dataclass(eq=False, slots=True)
class FakeAskTicket:
    """The lease one answer holds in memory."""

    session_id: str
    _holders: dict[str, FakeAskTicket]
    _guard: threading.Lock

    def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing."""
        with self._guard:
            if self._holders.get(self.session_id) is self:
                del self._holders[self.session_id]


class FakeAskLimiter:
    """The ask lease in memory: one holder per session, freed only by its own ticket."""

    def __init__(self) -> None:
        """Start with no session answering."""
        self._holders: dict[str, FakeAskTicket] = {}
        self._guard = threading.Lock()

    def admit(self, session_id: str) -> FakeAskTicket | RetryAfter:
        """Hand out the session's ticket, or say when to retry."""
        with self._guard:
            if session_id in self._holders:
                return RetryAfter(BUSY_RETRY_SECONDS)
            ticket = FakeAskTicket(session_id, self._holders, self._guard)
            self._holders[session_id] = ticket
            return ticket

    def lose_every_lease(self) -> None:
        """Forget every holder, as a lease does when it expires."""
        with self._guard:
            self._holders.clear()


class UnavailableAskLimiter:
    """An ask lease that cannot be told."""

    def admit(self, session_id: str) -> FakeAskTicket | RetryAfter:
        """Raise: the lease store does not answer."""
        raise CoPresenterUnavailableError
