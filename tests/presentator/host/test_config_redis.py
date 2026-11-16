"""The Redis setting: a URL without credentials, and one the log never quotes."""

import pytest

from presentator.host.config import load_settings
from tests.presentator.host.support.config_refusals import (
    assert_refusal_names,
    assert_refusal_never_quotes,
)

_URL = "PRESENTATOR_REDIS_URL"
_A_PASSWORD = "the-password-only-this-test-has"  # noqa: S105 - a marker the refusals must not quote


@pytest.mark.parametrize(
    "given",
    [
        "redis://redis:6379/0",
        "redis://redis",
        "rediss://redis.internal:6380/3",
    ],
)
def test_a_redis_url_of_a_host_and_at_most_a_database_is_served(
    environment: pytest.MonkeyPatch, given: str
) -> None:
    environment.setenv(_URL, given)

    assert load_settings().redis_url == given


def test_a_missing_url_refuses_naming_the_field(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.delenv(_URL)

    assert_refusal_names(load_settings, "redis_url")


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param(f"redis://:{_A_PASSWORD}@redis:6379/0", id="password"),
        pytest.param(f"redis://user:{_A_PASSWORD}@redis/0", id="user and password"),
        pytest.param("http://redis:6379/0", id="another scheme"),
        pytest.param("redis:///0", id="no host"),
        pytest.param(f"redis://redis/0?password={_A_PASSWORD}", id="query"),
        pytest.param(f"redis://redis/0#{_A_PASSWORD}", id="fragment"),
        pytest.param(f"redis://redis/{_A_PASSWORD}", id="no database number"),
        pytest.param(f"redis://redis:{_A_PASSWORD}/0", id="port"),
    ],
)
def test_a_url_that_cannot_name_the_redis_refuses_without_quoting_it(
    environment: pytest.MonkeyPatch, refused: str
) -> None:
    environment.setenv(_URL, refused)

    assert_refusal_never_quotes(load_settings, _A_PASSWORD, refused)
