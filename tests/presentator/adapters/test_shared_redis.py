"""The Redis every pod shares holds the ask lease and the request budgets."""

import socket
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import timedelta
from threading import Thread

import pytest
from pydantic import SecretStr
from redis import Redis

from presentator.adapters.shared_redis import (
    KEY_PREFIX,
    RateLimitedHost,
    SharedRedis,
)
from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from presentator.ports.copresenter import AskLimiter, AskTicket
from tests.presentator.agent.support.driving import on_an_event_loop
from tests.presentator.application.support.ask_leases import FakeAskLimiter
from tests.support.redis import (
    NOTHING_LISTENS_HERE,
    opened,
    relay_in_front_of,
    stored_keys,
)

_A_SESSION = "session"
_ANOTHER_SESSION = "another-session"
_LASTS = timedelta(seconds=120)


@asynccontextmanager
async def _shared(url: str) -> AsyncGenerator[SharedRedis]:
    redis = SharedRedis(url)
    try:
        yield redis
    finally:
        await redis.aclose()
        redis.close()


async def _admitted(limiter: AskLimiter, session_id: str) -> AskTicket:
    ticket = await limiter.admit(session_id)
    assert not isinstance(ticket, RetryAfter)
    return ticket


@pytest.fixture
def shared(redis_url: str) -> Iterator[SharedRedis]:
    redis = SharedRedis(redis_url)
    yield redis
    redis.close()


@pytest.fixture
def server(redis_url: str) -> Iterator[Redis]:
    with opened(redis_url) as redis:
        yield redis


type LeaseOf = Callable[[], AbstractAsyncContextManager[AskLimiter]]


@pytest.fixture(params=["in memory", "redis"])
def lease_of(request: pytest.FixtureRequest) -> LeaseOf:
    """The in-memory double and the Redis adapter, which one contract holds for."""
    if request.param == "in memory":

        @asynccontextmanager
        async def in_memory() -> AsyncGenerator[AskLimiter]:
            yield FakeAskLimiter()

        return in_memory

    redis_url: str = request.getfixturevalue("redis_url")

    @asynccontextmanager
    async def in_redis() -> AsyncGenerator[AskLimiter]:
        async with _shared(redis_url) as shared:
            yield shared.ask_limiter(lasts=_LASTS)

    return in_redis


@on_an_event_loop
async def test_a_session_whose_answer_is_running_is_told_when_to_retry(
    lease_of: LeaseOf,
) -> None:
    async with lease_of() as limiter:
        await limiter.admit(_A_SESSION)

        assert await limiter.admit(_A_SESSION) == RetryAfter(BUSY_RETRY_SECONDS)


@on_an_event_loop
async def test_a_session_is_admitted_again_once_its_ticket_is_released(
    lease_of: LeaseOf,
) -> None:
    async with lease_of() as limiter:
        ticket = await _admitted(limiter, _A_SESSION)

        await ticket.release()

        assert not isinstance(await limiter.admit(_A_SESSION), RetryAfter)


@on_an_event_loop
async def test_one_session_answering_leaves_another_free(lease_of: LeaseOf) -> None:
    async with lease_of() as limiter:
        await limiter.admit(_A_SESSION)

        assert not isinstance(await limiter.admit(_ANOTHER_SESSION), RetryAfter)


@on_an_event_loop
async def test_a_second_release_of_one_ticket_does_nothing(
    lease_of: LeaseOf,
) -> None:
    async with lease_of() as limiter:
        first = await _admitted(limiter, _A_SESSION)
        await first.release()
        await limiter.admit(_A_SESSION)

        await first.release()

        assert await limiter.admit(_A_SESSION) == RetryAfter(BUSY_RETRY_SECONDS)


@on_an_event_loop
async def test_more_than_twenty_questions_in_a_row_are_admitted(
    lease_of: LeaseOf,
) -> None:
    async with lease_of() as limiter:
        for _ in range(21):
            ticket = await _admitted(limiter, _A_SESSION)
            await ticket.release()


@on_an_event_loop
async def test_a_running_hold_lapses_after_the_lasts_the_pod_passes(
    redis_url: str, server: Redis
) -> None:
    async with _shared(redis_url) as shared:
        await shared.ask_limiter(lasts=_LASTS).admit(_A_SESSION)

        (key,) = stored_keys(server)
        assert 0 < server.pttl(key) <= _LASTS // timedelta(milliseconds=1)


@on_an_event_loop
async def test_every_key_starts_with_the_app_prefix(
    redis_url: str, server: Redis
) -> None:
    async with _shared(redis_url) as shared:
        await shared.ask_limiter(lasts=_LASTS).admit(_A_SESSION)
        for host in RateLimitedHost:
            shared.rate_limits(host).is_allowed("client", limit=5, window_seconds=60)

    assert all(key.startswith(f"{KEY_PREFIX}:") for key in stored_keys(server))
    assert len(stored_keys(server)) == 1 + len(RateLimitedHost)


def test_a_budget_spent_through_one_pod_is_refused_at_another_for_the_same_host(
    redis_url: str, shared: SharedRedis
) -> None:
    other_pod = SharedRedis(redis_url)
    try:
        spent = shared.rate_limits(RateLimitedHost.LOBBY).is_allowed(
            "client", limit=1, window_seconds=60
        )
        again = other_pod.rate_limits(RateLimitedHost.LOBBY).is_allowed(
            "client", limit=1, window_seconds=60
        )
        other_host = other_pod.rate_limits(RateLimitedHost.TALK).is_allowed(
            "client", limit=1, window_seconds=60
        )
    finally:
        other_pod.close()

    assert (spent, again, other_host) == (True, False, True)


def test_a_redis_that_answers_is_ready(shared: SharedRedis) -> None:
    assert shared.is_ready()


@on_an_event_loop
async def test_an_unreachable_redis_is_not_ready_and_cannot_tell_the_lease() -> None:
    async with _shared(NOTHING_LISTENS_HERE) as redis:
        ready = redis.is_ready()
        limiter = redis.ask_limiter(lasts=_LASTS)
        with pytest.raises(CoPresenterUnavailableError):
            await limiter.admit(_A_SESSION)

    assert not ready


@pytest.mark.parametrize(
    "in_the_url", [False, True], ids=["from-its-file", "in-the-url"]
)
def test_the_password_is_sent_to_the_server_on_connect(*, in_the_url: bool) -> None:
    password = "the-password-only-this-test-has"  # noqa: S105 - a marker the server must receive
    with socket.create_server(("127.0.0.1", 0)) as listener:
        port = listener.getsockname()[1]
        received: list[bytes] = []

        def listen() -> None:
            connection, _ = listener.accept()
            with connection:
                received.append(connection.recv(4096))

        thread = Thread(target=listen)
        thread.start()
        redis = (
            SharedRedis(f"redis://:{password}@127.0.0.1:{port}/0")
            if in_the_url
            else SharedRedis(f"redis://127.0.0.1:{port}/0", SecretStr(password))
        )
        try:
            redis.is_ready()
        finally:
            redis.close()
            thread.join()

    assert password.encode() in received[0]


@on_an_event_loop
async def test_a_ticket_released_while_redis_is_out_raises_nothing(
    redis_url: str,
) -> None:
    with relay_in_front_of(redis_url) as hop:
        async with _shared(hop.url) as redis:
            ticket = await _admitted(redis.ask_limiter(lasts=_LASTS), _A_SESSION)
            hop.sever()

            await ticket.release()
            ready = redis.is_ready()

    assert not ready
