"""A refused configuration ends the start in one JSON line naming the setting."""

from collections.abc import Callable
from pathlib import Path

import pytest

from presentator.host import main
from tests.support.lobby import A_BROWSERS_ADDRESS
from tests.support.log_lines import LoggedText, events_of

type Arrangement = Callable[[pytest.MonkeyPatch, Path], str]

_AN_HTTP_ORIGIN = "http://presentator.test"
_A_DATABASE_URL = "postgresql://somebody@database.test/presentator"


def _http_public_origin(environment: pytest.MonkeyPatch, directory: Path) -> str:
    del directory
    environment.setenv("PRESENTATOR_PUBLIC_ORIGIN", _AN_HTTP_ORIGIN)
    return _AN_HTTP_ORIGIN


def _talk_on_the_lobby_hostname(
    environment: pytest.MonkeyPatch, directory: Path
) -> str:
    del directory
    environment.setenv("PRESENTATOR_TALK_ORIGIN", A_BROWSERS_ADDRESS)
    return A_BROWSERS_ADDRESS


def _unreadable_password_file(environment: pytest.MonkeyPatch, directory: Path) -> str:
    password_file = str(directory / "no-password-here")
    environment.setenv("PRESENTATOR_DATABASE_URL", _A_DATABASE_URL)
    environment.setenv("PRESENTATOR_DATABASE_PASSWORD_FILE", password_file)
    return password_file


def _an_unknown_agent_setting(environment: pytest.MonkeyPatch, directory: Path) -> str:
    del directory
    unknown = "a-value-no-setting-owns"
    environment.setenv("PRESENTATOR_AGENT__BACKENDS", unknown)
    return unknown


def _an_issuer_the_sign_in_client_refuses(
    environment: pytest.MonkeyPatch, directory: Path
) -> str:
    del directory
    # The settings take the host; the client refuses it as no plain host.
    issuer = "https://sign_in.presentator.test/realms/refused"
    environment.setenv("PRESENTATOR_OIDC_ISSUER", issuer)
    return issuer


def _no_key_file(environment: pytest.MonkeyPatch, directory: Path) -> str:
    environment.delenv("PRESENTATOR_SECRET_KEY_FILE")
    return str(directory)


def _a_port_no_socket_binds(unbindable: str) -> Arrangement:
    def arrange(environment: pytest.MonkeyPatch, directory: Path) -> str:
        del directory
        environment.setenv("PRESENTATOR_PORT", unbindable)
        return unbindable

    return arrange


def _a_log_level_logline_does_not_name(
    environment: pytest.MonkeyPatch, directory: Path
) -> str:
    del directory
    unnamed = "chatty"
    environment.setenv("LOG_LEVEL", unnamed)
    return unnamed


def _a_statement_timeout_of(refused: str) -> Arrangement:
    def arrange(environment: pytest.MonkeyPatch, directory: Path) -> str:
        del directory
        environment.setenv("PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS", refused)
        return refused

    return arrange


def _no_database(environment: pytest.MonkeyPatch, directory: Path) -> str:
    environment.delenv("PRESENTATOR_DATABASE_URL")
    environment.delenv("PRESENTATOR_DATABASE_PASSWORD_FILE")
    return str(directory)


def _no_bucket(environment: pytest.MonkeyPatch, directory: Path) -> str:
    environment.delenv("PRESENTATOR_S3_BUCKET")
    return str(directory)


def _unparsable_object_store_key(
    environment: pytest.MonkeyPatch, directory: Path
) -> str:
    key_file = directory / "object-store-key"
    key_file.write_text("aws_access_key_id = no section holds this\n")
    environment.setenv("AWS_SHARED_CREDENTIALS_FILE", str(key_file))
    return str(key_file)


@pytest.mark.parametrize(
    ("arguments", "arrange", "setting"),
    [
        ([], _http_public_origin, "PRESENTATOR_PUBLIC_ORIGIN"),
        ([], _talk_on_the_lobby_hostname, "PRESENTATOR_TALK_ORIGIN"),
        ([], _unreadable_password_file, "PRESENTATOR_DATABASE_PASSWORD_FILE"),
        ([], _no_key_file, "PRESENTATOR_SECRET_KEY_FILE"),
        ([], _an_unknown_agent_setting, "PRESENTATOR_AGENT__BACKENDS"),
        ([], _a_port_no_socket_binds("1000000"), "PRESENTATOR_PORT"),
        ([], _a_port_no_socket_binds("-5"), "PRESENTATOR_PORT"),
        ([], _a_log_level_logline_does_not_name, "LOG_LEVEL"),
        (["migrate"], _a_log_level_logline_does_not_name, "LOG_LEVEL"),
        (
            [],
            _an_issuer_the_sign_in_client_refuses,
            (
                "PRESENTATOR_OIDC_ISSUER,PRESENTATOR_OIDC_CLIENT_ID,"
                "PRESENTATOR_OIDC_CLIENT_SECRET_FILE,PRESENTATOR_SPEECH_SERVICE_SCOPE,"
                "PRESENTATOR_PUBLIC_ORIGIN,PRESENTATOR_TALK_ORIGIN"
            ),
        ),
        (
            [],
            _no_database,
            "PRESENTATOR_DATABASE_URL,PRESENTATOR_DATABASE_PASSWORD_FILE",
        ),
        ([], _no_bucket, "PRESENTATOR_S3_BUCKET"),
        # Postgres reads zero as no bound at all; written so that the log's own
        # digits never spell it.
        (
            [],
            _a_statement_timeout_of("0e0"),
            "PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS",
        ),
        # Postgres holds no timeout past 2**31 - 1 milliseconds, the wait for
        # the refresh's default build bound added.
        *(
            (
                [],
                _a_statement_timeout_of(past_range),
                "PRESENTATOR_DATABASE_STATEMENT_TIMEOUT_SECONDS",
            )
            for past_range in ("2147483", "3000000", "1e9")
        ),
        (
            [],
            _unparsable_object_store_key,
            "AWS_SHARED_CREDENTIALS_FILE,AWS_CONFIG_FILE",
        ),
        (
            ["migrate"],
            _no_database,
            "PRESENTATOR_DATABASE_URL,PRESENTATOR_DATABASE_PASSWORD_FILE",
        ),
    ],
)
@pytest.mark.usefixtures("served_database")
def test_a_refused_configuration_ends_in_one_failed_line_naming_the_setting(  # noqa: PLR0913, PLR0917 - the fixtures and the case
    environment: pytest.MonkeyPatch,
    tmp_path: Path,
    logged: LoggedText,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    arrange: Arrangement,
    setting: str,
) -> None:
    never_logged = arrange(environment, tmp_path)

    with pytest.raises(SystemExit) as ended:
        main.main(arguments)

    assert ended.value.code == 1
    (failed,) = events_of(logged, "process.failed")
    assert (failed["level"], failed["reason"]) == ("error", "configuration_refused")
    assert failed["setting"] == setting
    assert never_logged not in logged.text
    assert not capsys.readouterr().err
