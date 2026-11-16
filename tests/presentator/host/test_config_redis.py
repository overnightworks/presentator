"""The Redis setting: a URL without credentials, and one the log never quotes."""

from pathlib import Path

import pytest

from presentator.host.config import ConfigurationError, load_settings
from tests.presentator.host.support.config_refusals import (
    assert_refusal_names,
    assert_refusal_never_quotes,
)

_URL = "PRESENTATOR_REDIS_URL"
_PASSWORD_FILE = "PRESENTATOR_REDIS_PASSWORD_FILE"  # noqa: S105 - a variable name
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


def test_a_user_named_in_the_url_is_served(environment: pytest.MonkeyPatch) -> None:
    environment.setenv(_URL, "redis://presentator@redis:6379/0")

    assert load_settings().redis_url == "redis://presentator@redis:6379/0"


def test_the_password_comes_from_its_file_minus_one_trailing_newline(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    password_file = tmp_path / "redis-password"
    password_file.write_text(f"{_A_PASSWORD}\n", encoding="utf-8")
    environment.setenv(_PASSWORD_FILE, str(password_file))

    password = load_settings().redis_password

    assert password is not None
    assert password.get_secret_value() == _A_PASSWORD


def test_a_redis_that_asks_for_no_password_has_none(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.delenv(_PASSWORD_FILE, raising=False)

    assert load_settings().redis_password is None


def test_a_password_file_that_cannot_be_read_refuses_without_quoting_its_path(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    absent = tmp_path / "absent"
    environment.setenv(_PASSWORD_FILE, str(absent))

    with pytest.raises(ConfigurationError, match="redis_password_file") as raised:
        _ = load_settings().redis_password

    assert str(absent) not in str(raised.value)


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
