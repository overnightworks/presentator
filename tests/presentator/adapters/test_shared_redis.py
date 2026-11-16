"""The Redis every pod shares holds the ask lease and the request budgets."""

import socket
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Barrier, Event, Thread

import pytest
from pydantic import SecretStr
from redis import Redis

from presentator.adapters import shared_redis
from presentator.adapters.shared_redis import (
    KEY_ROOT,
    LEASE_MILLISECONDS,
    RateLimitedHost,
    SharedRedis,
)
from presentator.contracts.copresenter import (
    BUSY_RETRY_SECONDS,
    CoPresenterUnavailableError,
    RetryAfter,
)
from presentator.ports.copresenter import AskLimiter
from tests.presentator.application.support.fakes import FakeAskLimiter
from tests.support.redis import (
    NOTHING_LISTENS_HERE,
    empty,
    opened,
    relay_in_front_of,
    stored_keys,
)

_THREADS = 8
_A_SESSION = "session"
_ANOTHER_SESSION = "another-session"


@dataclass(frozen=True, slots=True)
class Lease:
    """A limiter and how a test makes its leases run out."""

    limiter: AskLimiter
    expire_every_lease: Callable[[], None]


@pytest.fixture
def shared(redis_url: str) -> Iterator[SharedRedis]:
    redis = SharedRedis(redis_url)
    yield redis
    redis.close()


@pytest.fixture
def server(redis_url: str) -> Iterator[Redis]:
    with opened(redis_url) as redis:
        yield redis


@pytest.fixture(params=["in memory", "redis"])
def lease(request: pytest.FixtureRequest) -> Iterator[Lease]:
    """The in-memory double and the Redis adapter, which one contract holds for."""
    if request.param == "in memory":
        limiter = FakeAskLimiter()
        yield Lease(limiter, limiter.lose_every_lease)
        return
    shared: SharedRedis = request.getfixturevalue("shared")
    server: Redis = request.getfixturevalue("server")
    yield Lease(shared.ask_limiter(), lambda: empty(server))


def test_a_session_whose_answer_is_running_is_told_when_to_retry(
    lease: Lease,
) -> None:
    lease.limiter.admit(_A_SESSION)

    assert lease.limiter.admit(_A_SESSION) == RetryAfter(BUSY_RETRY_SECONDS)


def test_a_session_is_admitted_again_once_its_ticket_is_released(
    lease: Lease,
) -> None:
    ticket = lease.limiter.admit(_A_SESSION)
    assert not isinstance(ticket, RetryAfter)

    ticket.release()

    assert not isinstance(lease.limiter.admit(_A_SESSION), RetryAfter)


def test_one_session_answering_leaves_another_free(lease: Lease) -> None:
    lease.limiter.admit(_A_SESSION)

    assert not isinstance(lease.limiter.admit(_ANOTHER_SESSION), RetryAfter)


def test_a_session_whose_lease_ran_out_is_admitted_again(lease: Lease) -> None:
    lease.limiter.admit(_A_SESSION)

    lease.expire_every_lease()

    assert not isinstance(lease.limiter.admit(_A_SESSION), RetryAfter)


def test_a_released_old_ticket_cannot_free_the_lease_of_a_newer_answer(
    lease: Lease,
) -> None:
    old = lease.limiter.admit(_A_SESSION)
    assert not isinstance(old, RetryAfter)
    lease.expire_every_lease()
    lease.limiter.admit(_A_SESSION)

    old.release()
    old.release()

    assert lease.limiter.admit(_A_SESSION) == RetryAfter(BUSY_RETRY_SECONDS)


def test_more_than_twenty_questions_in_a_row_are_admitted(lease: Lease) -> None:
    for _ in range(21):
        ticket = lease.limiter.admit(_A_SESSION)
        assert not isinstance(ticket, RetryAfter)
        ticket.release()


def test_simultaneous_asks_of_one_session_on_many_threads_admit_exactly_one(
    lease: Lease,
) -> None:
    start = Barrier(_THREADS)

    def ask() -> object:
        start.wait()
        return lease.limiter.admit(_A_SESSION)

    with ThreadPoolExecutor(max_workers=_THREADS) as pool:
        outcomes = [
            future.result() for future in [pool.submit(ask) for _ in range(_THREADS)]
        ]

    refused = [outcome for outcome in outcomes if isinstance(outcome, RetryAfter)]
    assert len(refused) == _THREADS - 1


def test_a_lease_taken_by_one_pod_refuses_the_session_at_another(
    redis_url: str, shared: SharedRedis
) -> None:
    other_pod = SharedRedis(redis_url)
    try:
        shared.ask_limiter().admit(_A_SESSION)

        refused = other_pod.ask_limiter().admit(_A_SESSION)
    finally:
        other_pod.close()

    assert refused == RetryAfter(BUSY_RETRY_SECONDS)


def test_a_running_answers_lease_has_a_bounded_life_and_an_ended_one_leaves_no_key(
    shared: SharedRedis, server: Redis
) -> None:
    ended = shared.ask_limiter().admit(_A_SESSION)
    assert not isinstance(ended, RetryAfter)
    running = shared.ask_limiter().admit(_ANOTHER_SESSION)
    assert not isinstance(running, RetryAfter)

    ended.release()

    (key,) = stored_keys(server)
    assert 0 < server.pttl(key) <= LEASE_MILLISECONDS


def test_an_answer_that_outlives_the_lease_keeps_its_session(
    shared: SharedRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    lease_milliseconds = 600
    monkeypatch.setattr(shared_redis, "LEASE_MILLISECONDS", lease_milliseconds)
    ticket = shared.ask_limiter().admit(_A_SESSION)
    assert not isinstance(ticket, RetryAfter)

    Event().wait(timeout=3 * lease_milliseconds / 1000)

    refused = shared.ask_limiter().admit(_A_SESSION)
    ticket.release()
    assert refused == RetryAfter(BUSY_RETRY_SECONDS)
    assert not isinstance(shared.ask_limiter().admit(_A_SESSION), RetryAfter)


def test_every_key_starts_with_the_versioned_root(
    shared: SharedRedis, server: Redis
) -> None:
    shared.ask_limiter().admit(_A_SESSION)
    for host in RateLimitedHost:
        shared.rate_limits(host).is_allowed("client", limit=5, window_seconds=60)

    keys = stored_keys(server)

    assert len(keys) == 1 + len(RateLimitedHost)
    assert all(key.startswith(f"{KEY_ROOT}:") for key in keys)


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


def test_a_redis_that_cannot_be_reached_is_not_ready_and_cannot_tell_the_lease() -> (
    None
):
    redis = SharedRedis(NOTHING_LISTENS_HERE)
    try:
        ready = redis.is_ready()
        with pytest.raises(CoPresenterUnavailableError):
            redis.ask_limiter().admit(_A_SESSION)
    finally:
        redis.close()

    assert not ready


def test_the_password_is_sent_to_the_server_on_connect() -> None:
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
        redis = SharedRedis(f"redis://127.0.0.1:{port}/0", SecretStr(password))
        try:
            redis.is_ready()
        finally:
            redis.close()
            thread.join()

    assert password.encode() in received[0]


def test_a_ticket_released_while_redis_is_out_raises_nothing(
    redis_url: str,
) -> None:
    with relay_in_front_of(redis_url) as hop:
        redis = SharedRedis(hop.url)
        try:
            ticket = redis.ask_limiter().admit(_A_SESSION)
            assert not isinstance(ticket, RetryAfter)
            hop.sever()

            ticket.release()
            ready = redis.is_ready()
        finally:
            redis.close()

    assert not ready
