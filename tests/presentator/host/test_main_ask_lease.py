"""The ask lease outlives the longest admitted answer by the host's margin."""

from collections.abc import Iterator
from typing import cast

import pytest
from agents.agent_file import load_agent
from redis import Redis

from presentator.adapters.shared_redis import SharedRedis
from presentator.agent.backend import COPRESENTER_AGENT_FILE, SHOW_SLIDE
from presentator.contracts.copresenter import RetryAfter
from presentator.host.main import ASK_LEASE_MARGIN_SECONDS, ask_limiter_of
from tests.presentator.agent.support.driving import on_an_event_loop
from tests.support.redis import opened, stored_keys

_MILLISECONDS = 1000


@pytest.fixture
def server(redis_url: str) -> Iterator[Redis]:
    with opened(redis_url) as redis:
        yield redis


@on_an_event_loop
async def test_the_lease_lasts_the_agents_deadline_plus_the_margin(
    redis_url: str, server: Redis
) -> None:
    deadline = load_agent(
        COPRESENTER_AGENT_FILE, [SHOW_SLIDE.declaration]
    ).max_duration_seconds
    redis = SharedRedis(redis_url)
    try:
        ticket = await ask_limiter_of(redis, deadline_seconds=deadline).admit("session")
        (key,) = stored_keys(server)
        holds = cast(
            "list[tuple[bytes, float]]",
            server.zrange(key, 0, -1, withscores=True),  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped
        )
        seconds, microseconds = server.time()  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped
    finally:
        await redis.aclose()
        redis.close()

    assert not isinstance(ticket, RetryAfter)
    ((_hold, expires_at),) = holds
    lasts = expires_at - (seconds * _MILLISECONDS + microseconds // _MILLISECONDS)
    assert (
        deadline * _MILLISECONDS
        < lasts
        <= (deadline + ASK_LEASE_MARGIN_SECONDS) * _MILLISECONDS
    )
