"""The database settings: a URL without a password, the password in a file."""

import re
from pathlib import Path

import pytest

from presentator.host.config import ConfigurationError, load_database_settings

_URL = "PRESENTATOR_DATABASE_URL"
_PASSWORD_FILE = "PRESENTATOR_DATABASE_PASSWORD_FILE"  # noqa: S105 - a variable name
A_PASSWORD = "the-password-only-this-test-has"  # noqa: S105 - a marker the refusals must not quote
A_DATABASE = "postgresql://presentator@postgres:5432/presentator"


@pytest.fixture
def database_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> pytest.MonkeyPatch:
    password_file = tmp_path / "password"
    password_file.write_text(f"{A_PASSWORD}\n", encoding="utf-8")
    monkeypatch.setenv(_URL, A_DATABASE)
    monkeypatch.setenv(_PASSWORD_FILE, str(password_file))
    return monkeypatch


@pytest.mark.usefixtures("database_variables")
def test_the_url_and_the_password_file_give_the_settings() -> None:
    settings = load_database_settings()

    assert settings.database_url == A_DATABASE
    assert settings.database_password.get_secret_value() == A_PASSWORD


@pytest.mark.parametrize("variable", [_URL, _PASSWORD_FILE])
def test_a_missing_value_refuses_naming_the_field(
    database_variables: pytest.MonkeyPatch, variable: str
) -> None:
    database_variables.delenv(variable)

    with pytest.raises(ConfigurationError, match=re.escape(variable.lower()[13:])):
        load_database_settings()


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
def test_a_url_that_cannot_name_the_database_refuses_without_quoting_it(
    database_variables: pytest.MonkeyPatch, refused: str
) -> None:
    database_variables.setenv(_URL, refused)

    with pytest.raises(ConfigurationError) as raised:
        load_database_settings()

    assert A_PASSWORD not in str(raised.value)
    assert refused not in str(raised.value)


def test_a_password_file_that_cannot_be_read_refuses_without_quoting_its_path(
    database_variables: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    absent = tmp_path / "absent"
    database_variables.setenv(_PASSWORD_FILE, str(absent))

    with pytest.raises(ConfigurationError, match="database_password_file") as raised:
        load_database_settings()

    assert str(absent) not in str(raised.value)
