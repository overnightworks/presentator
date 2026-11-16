"""The Redis every pod shares: the request limits and the ask lease.

It holds nothing durable, so a restart resets the limit windows and frees every
lease. Every key starts with `KEY_ROOT`, whose version a later release raises
beside the old one for one release.
"""

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from pydantic import SecretStr
from redis import Redis
from redis.backoff import ExponentialBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from webauth.ports import RateLimitBackend
from webauth.rate_limit import RedisRateLimitBackend

from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from presentator.ports.copresenter import AskLimiter, AskTicket

KEY_VERSION: Final = "v1"
KEY_ROOT: Final = f"presentator:{KEY_VERSION}"
# A pod that dies mid-answer leaves its lease behind, and this is how long a
# session waits for it; a pod that lives renews it while it answers and frees it
# at the end.
LEASE_MILLISECONDS: Final = 120_000

# A check holds its worker thread until the client gives up, so the timeouts
# are short and the retries few: an outage answers at once and pins no thread.
_TIMEOUT_SECONDS: Final = 0.5
_RETRIES: Final = 1
# Frees the lease only while it still holds this ticket's token, in one step.
_RELEASE_IF_OWN: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
# Extends the lease only while it still holds this ticket's token, in one step.
_RENEW_IF_OWN: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('PEXPIRE', KEYS[1], ARGV[2])
end
return 0
"""
# A renewal that fails once still leaves two more before the lease runs out.
_RENEWALS_PER_LEASE: Final = 3
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LeaseTiming:
    """How long a lease lasts and the clock and pause that measure it.

    Only a test replaces the clock and the pause, to advance a lease without
    waiting for it.
    """

    milliseconds: int = LEASE_MILLISECONDS
    clock: Callable[[], float] = time.monotonic
    # Waits up to the given seconds for the event; true when the event was set.
    pause: Callable[[threading.Event, float], bool] = threading.Event.wait


class RateLimitedHost(StrEnum):
    """The two origins of one pod, each spending a budget of its own."""

    LOBBY = "lobby"
    TALK = "talk"


class _LeaseTicket:
    """The lease one answer holds, freed only by the token it was taken with.

    The lease is renewed for as long as the ticket lives, so a long answer keeps
    its session; a pod that dies stops renewing and the lease expires. The
    ticket holds the session only until the last confirmed renewal runs out: a
    Redis that stays unreachable for a whole lease lets another pod take it.
    """

    def __init__(  # noqa: PLR0913, PLR0917 - the limiter hands over the lease it took
        self,
        session_id: str,
        redis: Redis,
        key: str,
        token: str,
        timing: LeaseTiming,
        taken_at: float,
    ) -> None:
        """Hold the lease at `key`, taken at `taken_at`, and start renewing it."""
        self.session_id = session_id
        self._redis = redis
        self._key = key
        self._token = token
        self._timing = timing
        self._held_until = taken_at + timing.milliseconds / 1000
        self._released = threading.Event()
        self._taken_by_another = threading.Event()
        threading.Thread(target=self._renew_until_released, daemon=True).start()

    def still_holds(self) -> bool:
        """Whether the session is still this ticket's by Redis's last confirmation."""
        return (
            not self._released.is_set()
            and not self._taken_by_another.is_set()
            and self._timing.clock() < self._held_until
        )

    def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing.

        A Redis that does not answer leaves the lease to expire on its own.
        """
        self._released.set()
        try:
            self._redis.eval(_RELEASE_IF_OWN, 1, self._key, self._token)
        except RedisError as failure:
            _LOGGER.warning("ask lease release failed: %s", type(failure).__name__)

    def _renew_until_released(self) -> None:
        interval = self._timing.milliseconds / _RENEWALS_PER_LEASE / 1000
        while not self._timing.pause(self._released, interval):
            # Redis counts the new lease from after this moment, so the ticket
            # never believes it holds the session longer than Redis does.
            asked_at = self._timing.clock()
            try:
                holds = self._redis.eval(
                    _RENEW_IF_OWN, 1, self._key, self._token, self._timing.milliseconds
                )
            except RedisError as failure:
                _LOGGER.warning("ask lease renewal failed: %s", type(failure).__name__)
                continue
            if not holds:
                self._taken_by_another.set()
                continue
            self._held_until = asked_at + self._timing.milliseconds / 1000


@dataclass(frozen=True, slots=True)
class _RedisAskLimiter:
    _redis: Redis
    _timing: LeaseTiming

    def admit(self, session_id: str) -> AskTicket | RetryAfter:
        key = f"{KEY_ROOT}:ask:{session_id}"
        token = uuid.uuid4().hex
        asked_at = self._timing.clock()
        try:
            taken = self._redis.set(key, token, nx=True, px=self._timing.milliseconds)
        except RedisError as failure:
            _LOGGER.warning("ask lease failed: %s", type(failure).__name__)
            raise CoPresenterUnavailableError from None
        if not taken:
            return RetryAfter(BUSY_RETRY_SECONDS)
        return _LeaseTicket(session_id, self._redis, key, token, self._timing, asked_at)


class SharedRedis:
    """The client of the one Redis the pods share; the password never rides the URL."""

    def __init__(
        self,
        url: str,
        password: SecretStr | None = None,
        *,
        lease: LeaseTiming = LeaseTiming(),  # noqa: B008 - an immutable default
    ) -> None:
        """Connect lazily: nothing is asked of Redis until a limit or a lease is."""
        self._lease = lease
        # redis-py's `from_url` leaves its keyword arguments untyped.
        self._redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            url,
            password=None if password is None else password.get_secret_value(),
            socket_timeout=_TIMEOUT_SECONDS,
            socket_connect_timeout=_TIMEOUT_SECONDS,
            retry=Retry(ExponentialBackoff(), _RETRIES),
        )

    def rate_limits(self, host: RateLimitedHost) -> RateLimitBackend:
        """The request and sign-in budgets of `host`, spent by every pod together."""
        return RedisRateLimitBackend(
            self._redis, key_prefix=f"{KEY_ROOT}:rate:{host.value}"
        )

    def ask_limiter(self) -> AskLimiter:
        """The lease of one answer per session, held across every pod."""
        return _RedisAskLimiter(self._redis, self._lease)

    def is_ready(self) -> bool:
        """Whether Redis answers; a pod that cannot count its limits serves nobody."""
        try:
            return bool(self._redis.ping())  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped
        except RedisError as failure:
            _LOGGER.warning("redis not ready: %s", type(failure).__name__)
            return False

    def close(self) -> None:
        """Close the connections this client opened."""
        self._redis.close()
