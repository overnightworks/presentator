"""The Redis every pod shares: the request limits and the ask lease.

It holds nothing durable, so a restart resets the limit windows and frees every
lease. Every key starts with `KEY_ROOT`, whose version a later release raises
beside the old one for one release.
"""

import logging
import uuid
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

# A check holds its worker thread until the client gives up, so the timeouts
# are short and the retries few: an outage answers at once and pins no thread.
_TIMEOUT_SECONDS: Final = 0.5
_RETRIES: Final = 1
_MILLISECONDS: Final = 1000
# Frees the lease only while it still holds this ticket's token, in one step.
_RELEASE_IF_OWN: Final = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""
_LOGGER = logging.getLogger(__name__)


class RateLimitedHost(StrEnum):
    """The two origins of one pod, each spending a budget of its own."""

    LOBBY = "lobby"
    TALK = "talk"


class _LeaseTicket:
    """The lease one answer holds, freed only by the token it was taken with.

    The lease is never renewed: its caller bounds the answer below the lease's
    life, and a pod that dies mid-answer leaves the lease to expire.
    """

    def __init__(self, session_id: str, redis: Redis, key: str, token: str) -> None:
        """Hold the lease at `key` taken with `token`."""
        self.session_id = session_id
        self._redis = redis
        self._key = key
        self._token = token

    def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing.

        A Redis that does not answer leaves the lease to expire on its own.
        """
        try:
            self._redis.eval(_RELEASE_IF_OWN, 1, self._key, self._token)
        except RedisError as failure:
            _LOGGER.warning("ask lease release failed: %s", type(failure).__name__)


@dataclass(frozen=True, slots=True)
class _RedisAskLimiter:
    _redis: Redis
    _lease_milliseconds: int

    def admit(self, session_id: str) -> AskTicket | RetryAfter:
        key = f"{KEY_ROOT}:ask:{session_id}"
        token = uuid.uuid4().hex
        try:
            taken = self._redis.set(key, token, nx=True, px=self._lease_milliseconds)
        except RedisError as failure:
            _LOGGER.warning("ask lease failed: %s", type(failure).__name__)
            raise CoPresenterUnavailableError from None
        if not taken:
            return RetryAfter(BUSY_RETRY_SECONDS)
        return _LeaseTicket(session_id, self._redis, key, token)


class SharedRedis:
    """The client of the one Redis the pods share."""

    def __init__(self, url: str, password: SecretStr | None = None) -> None:
        """Connect lazily: nothing is asked of Redis until a limit or a lease is."""
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

    def ask_limiter(self, *, lease_seconds: float) -> AskLimiter:
        """The lease of one answer per session, held across every pod.

        A lease that its answer does not free expires after `lease_seconds`.
        """
        return _RedisAskLimiter(self._redis, round(lease_seconds * _MILLISECONDS))

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
