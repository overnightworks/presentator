"""The database settings: a URL without a password, the password in a file.

The migrate command and the server read the same two variables, so each refusal
holds for both.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from presentator.host.config import (
    DatabaseSettings,
    Settings,
    load_database_settings,
    load_settings,
)
from presentator.host.environment import ConfigurationError
from tests.presentator.host.support.config_refusals import (
    assert_refusal_names,
    assert_refusal_never_quotes,
)

_URL = "PRESENTATOR_DATABASE_URL"
_PASSWORD_FILE = "PRESENTATOR_DATABASE_PASSWORD_FILE"  # noqa: S105 - a variable name
A_PASSWORD = "the-password-only-this-test-has"  # noqa: S105 - a marker the refusals must not quote
A_DATABASE = "postgresql://presentator@postgres:5432/presentator"
type Loader = Callable[[], Settings | DatabaseSettings]
_LOADERS: list[Loader] = [load_database_settings, load_settings]


@pytest.fixture
def database_variables(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    monkeypatch = environment
    password_file = tmp_path / "password"
    password_file.write_text(f"{A_PASSWORD}\n", encoding="utf-8")
    monkeypatch.setenv(_URL, A_DATABASE)
    monkeypatch.setenv(_PASSWORD_FILE, str(password_file))
    return monkeypatch


@pytest.mark.usefixtures("database_variables")
@pytest.mark.parametrize("load", _LOADERS)
def test_the_url_and_the_password_file_give_the_database(
    load: Loader,
) -> None:
    database = load().postgres

    assert database.url == A_DATABASE
    assert database.password.get_secret_value() == A_PASSWORD


@pytest.mark.parametrize(
    ("variable", "field"),
    [(_URL, "database_url"), (_PASSWORD_FILE, "database_password_file")],
)
@pytest.mark.parametrize("load", _LOADERS)
def test_a_missing_value_refuses_naming_the_field(
    database_variables: pytest.MonkeyPatch,
    load: Loader,
    variable: str,
    field: str,
) -> None:
    database_variables.delenv(variable)

    assert_refusal_names(load, field)


@pytest.mark.parametrize(
    "refused",
    [
        pytest.param(f"postgresql://presentator:{A_PASSWORD}@db/p", id="password"),
        pytest.param("mysql://presentator@db/p", id="another database"),
        pytest.param("postgresql://db/p", id="no user"),
        pytest.param("postgresql://presentator@/p", id="no host"),
        pytest.param("postgresql://presentator@db", id="no database"),
        pytest.param("postgresql://presentator@db/p?host=elsewhere", id="query"),
        pytest.param("postgresql://presentator@db:port/p", id="port"),
    ],
)
@pytest.mark.parametrize("load", _LOADERS)
def test_a_url_that_cannot_name_the_database_refuses_without_quoting_it(
    database_variables: pytest.MonkeyPatch, load: Loader, refused: str
) -> None:
    database_variables.setenv(_URL, refused)

    assert_refusal_never_quotes(load, A_PASSWORD, refused)


@pytest.mark.parametrize("load", _LOADERS)
def test_a_password_file_that_cannot_be_read_refuses_without_quoting_its_path(
    database_variables: pytest.MonkeyPatch, tmp_path: Path, load: Loader
) -> None:
    absent = tmp_path / "absent"
    database_variables.setenv(_PASSWORD_FILE, str(absent))

    with pytest.raises(ConfigurationError, match="database_password_file") as raised:
        load()

    assert str(absent) not in str(raised.value)
