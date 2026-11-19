"""The Redis every pod shares: the request limits and the ask lease.

It holds nothing durable, so a restart resets the limit windows and frees every
lease. Every key starts `presentator:`: the budgets under `presentator:v1:`,
whose version a later release raises beside the old one for one release, and the
ask lease under `presentator:tandem:v1:`, where tandem keeps one set of holds
for the whole app. The lease is never renewed: its caller bounds the answer
below `lasts`, a pod that dies mid-answer leaves its session refused until
`lasts` run out, and after a Redis restart one session can have more than one
answer for up to `lasts`.
"""

from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Final

from pydantic import SecretStr
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.backoff import ExponentialBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from tandem import Slots, StoreError
from webauth.ports import RateLimitBackend
from webauth.rate_limit import RedisRateLimitBackend

from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from presentator.ports.copresenter import AskLimiter, AskTicket

KEY_PREFIX: Final = "presentator"
_RATE_KEY_ROOT: Final = f"{KEY_PREFIX}:v1:rate"
_ASK_FAMILY: Final = "ask"

# A check holds its worker thread until the client gives up, so the timeouts
# are short and the retries few: an outage answers at once and pins no thread.
_TIMEOUT_SECONDS: Final = 0.5
_RETRIES: Final = 1


class RateLimitedHost(StrEnum):
    """The two origins of one pod, each spending a budget of its own."""

    LOBBY = "lobby"
    TALK = "talk"


@dataclass(slots=True, eq=False)
class _AskTicket:
    """The hold one answer has, freed only by the token it was taken with."""

    session_id: str
    _slots: Slots
    _token: str
    _released: bool = False

    async def release(self) -> None:
        """Free the session if this ticket still holds it; later calls do nothing.

        A Redis that does not answer leaves the hold to expire on its own.
        """
        if self._released:
            return
        self._released = True
        # tandem writes the failed release's line itself.
        with suppress(StoreError):
            await self._slots.release(self._token)


@dataclass(frozen=True, slots=True)
class _SlotAskLimiter:
    _slots: Slots

    async def admit(self, session_id: str) -> AskTicket | RetryAfter:
        try:
            token = await self._slots.take({session_id: 1})
        except StoreError:
            raise CoPresenterUnavailableError from None
        if token is None:
            return RetryAfter(BUSY_RETRY_SECONDS)
        return _AskTicket(session_id, self._slots, token)


class SharedRedis:
    """The clients of the one Redis the pods share, one blocking and one async."""

    def __init__(self, url: str, password: SecretStr | None = None) -> None:
        """Connect lazily: nothing is asked of Redis until a limit or a lease is."""
        secret = None if password is None else password.get_secret_value()
        # redis-py's `from_url` leaves its keyword arguments untyped.
        self._redis = Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
            url,
            password=secret,
            socket_timeout=_TIMEOUT_SECONDS,
            socket_connect_timeout=_TIMEOUT_SECONDS,
            retry=Retry(ExponentialBackoff(), _RETRIES),
        )
        # The async client's calls end only when its `Retry` gives up, so its
        # timeouts and retries are finite too.
        self._async_redis = AsyncRedis.from_url(  # pyright: ignore[reportUnknownMemberType]
            url,
            password=secret,
            socket_timeout=_TIMEOUT_SECONDS,
            socket_connect_timeout=_TIMEOUT_SECONDS,
            retry=AsyncRetry(ExponentialBackoff(), _RETRIES),
        )

    def rate_limits(self, host: RateLimitedHost) -> RateLimitBackend:
        """The request and sign-in budgets of `host`, spent by every pod together."""
        return RedisRateLimitBackend(
            self._redis, key_prefix=f"{_RATE_KEY_ROOT}:{host.value}"
        )

    def ask_limiter(self, *, lasts: timedelta) -> AskLimiter:
        """The lease of one answer per session, held across every pod.

        A hold that its answer does not free lapses after `lasts`, and every pod
        passes the same `lasts`.
        """
        return _SlotAskLimiter(
            Slots(
                self._async_redis,
                key_prefix=KEY_PREFIX,
                family=_ASK_FAMILY,
                lasts=lasts,
            )
        )

    def is_ready(self) -> bool:
        """Whether Redis answers; a pod that cannot count its limits serves nobody.

        It writes no line: the start line and the readiness answer carry the failure.
        """
        try:
            return bool(self._redis.ping())  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped
        except RedisError:
            return False

    def close(self) -> None:
        """Close the connections the blocking client opened."""
        self._redis.close()

    async def aclose(self) -> None:
        """Close the connections the async client opened, on the loop that used it."""
        await self._async_redis.aclose()
