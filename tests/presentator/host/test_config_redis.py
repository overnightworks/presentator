"""The Redis setting: a URL that may carry its password and no log quotes."""

import os
import subprocess
from pathlib import Path

import pytest

from presentator.host.config import load_settings
from presentator.host.environment import ConfigurationError
from tests.presentator.host.support.config_refusals import (
    assert_refusal_names,
    assert_refusal_never_quotes,
)
from tests.support.two_processes import CONSOLE_ENTRY_POINT

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

    assert load_settings().redis_url.get_secret_value() == given


def test_a_user_named_in_the_url_is_served(environment: pytest.MonkeyPatch) -> None:
    environment.setenv(_URL, "redis://presentator@redis:6379/0")

    assert (
        load_settings().redis_url.get_secret_value()
        == "redis://presentator@redis:6379/0"
    )


@pytest.mark.parametrize(
    "given",
    [
        f"redis://:{_A_PASSWORD}@redis:6379/0",
        f"redis://presentator:{_A_PASSWORD}@redis/0",
    ],
    ids=["password", "user and password"],
)
def test_a_url_carrying_the_password_is_served_and_never_shown(
    environment: pytest.MonkeyPatch, given: str
) -> None:
    environment.setenv(_URL, given)

    settings = load_settings()

    assert settings.redis_url.get_secret_value() == given
    assert _A_PASSWORD not in repr(settings) + str(settings.redis_url)


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
        pytest.param(f"redis://:{_A_PASSWORD}@redis:6379/0?x=1", id="password query"),
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


def test_a_refused_start_writes_neither_the_url_nor_its_password_anywhere(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv(_URL, f"redis://user:{_A_PASSWORD}@redis:6379/0?db=1")
    settings = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("PRESENTATOR_") and not name.startswith("PRESENTATOR_TEST_")
    }

    started = subprocess.run(  # noqa: S603 - the interpreter of this run
        [str(CONSOLE_ENTRY_POINT)],
        env={"PATH": os.environ["PATH"], **settings},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert started.returncode != 0
    assert not started.stderr
    assert f'"setting": "{_URL}"' in started.stdout
    assert _A_PASSWORD not in started.stdout + started.stderr
    assert "redis:6379" not in started.stdout + started.stderr
