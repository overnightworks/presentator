"""Two app processes on one Redis spend one budget, and need it to be ready."""

from http import HTTPStatus
from pathlib import Path

import pytest

from presentator.api.auth import LOBBY_RATE_LIMIT, START_SIGN_IN_PATH
from presentator.api.health import HEALTH_PATH
from tests.presentator.host.support.app_processes import Browser, serving_apps
from tests.support.redis import relay_in_front_of


@pytest.mark.usefixtures("environment")
def test_the_request_one_past_the_sign_in_budget_is_refused_at_either_process(
    database_url: str, tmp_path: Path
) -> None:
    budget = LOBBY_RATE_LIMIT.sign_in.max_requests
    with serving_apps(database_url, tmp_path) as apps:
        person = Browser()
        within = [
            person.request(apps[spent % len(apps)], "GET", START_SIGN_IN_PATH)
            for spent in range(budget)
        ]
        past = [person.request(app, "GET", START_SIGN_IN_PATH) for app in apps]

    assert HTTPStatus.TOO_MANY_REQUESTS not in {each.status_code for each in within}
    assert [each.status_code for each in past] == [HTTPStatus.TOO_MANY_REQUESTS] * 2


@pytest.mark.usefixtures("environment")
def test_both_processes_are_unready_while_redis_cannot_be_reached(
    database_url: str, redis_url: str, tmp_path: Path
) -> None:
    with (
        relay_in_front_of(redis_url) as redis,
        serving_apps(
            database_url, tmp_path, {"PRESENTATOR_REDIS_URL": redis.url}
        ) as apps,
    ):
        before = [Browser().request(app, "GET", HEALTH_PATH) for app in apps]
        redis.sever()
        after = [Browser().request(app, "GET", HEALTH_PATH) for app in apps]

    assert [each.status_code for each in before] == [HTTPStatus.OK] * 2
    assert [each.status_code for each in after] == [HTTPStatus.SERVICE_UNAVAILABLE] * 2
