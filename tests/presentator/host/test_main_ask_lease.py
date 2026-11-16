"""The ask lease outlives the longest admitted answer by the host's margin."""

from collections.abc import Iterator

import pytest
from agents.agent_file import load_agent
from redis import Redis

from presentator.adapters.shared_redis import SharedRedis
from presentator.agent.backend import COPRESENTER_AGENT_FILE, SHOW_SLIDE
from presentator.contracts.copresenter import RetryAfter
from presentator.host.main import ASK_LEASE_MARGIN_SECONDS, ask_limiter_of
from tests.support.redis import opened, stored_keys

_MILLISECONDS = 1000


@pytest.fixture
def server(redis_url: str) -> Iterator[Redis]:
    with opened(redis_url) as redis:
        yield redis


def test_the_lease_lasts_the_agents_deadline_plus_the_margin(
    redis_url: str, server: Redis
) -> None:
    deadline = load_agent(
        COPRESENTER_AGENT_FILE, [SHOW_SLIDE.declaration]
    ).max_duration_seconds
    redis = SharedRedis(redis_url)
    try:
        ticket = ask_limiter_of(redis, deadline_seconds=deadline).admit("session")
        (key,) = stored_keys(server)
        lived = server.pttl(key)
    finally:
        redis.close()

    assert not isinstance(ticket, RetryAfter)
    assert (
        deadline * _MILLISECONDS
        < lived
        <= (deadline + ASK_LEASE_MARGIN_SECONDS) * _MILLISECONDS
    )
