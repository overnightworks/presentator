"""The speech section: three required values, read from the environment or a `.env`."""

import os
import re
from pathlib import Path

import pytest

from presentator.host.config import ConfigurationError, load_settings
from tests.support.speech_service import A_SPEECH_ORIGIN, A_SPEECH_SCOPE, A_SPEECH_VOICE

_VARIABLES = (
    "PRESENTATOR_SPEECH_SERVICE",
    "PRESENTATOR_SPEECH_SERVICE_SCOPE",
    "PRESENTATOR_SPEECH_VOICE",
)


@pytest.mark.usefixtures("environment")
def test_the_three_speech_values_start_the_instance_with_no_network_call() -> None:
    """S1: reading the settings reaches nobody."""
    settings = load_settings()

    assert (
        settings.speech_service,
        settings.speech_service_scope,
        settings.speech_voice,
    ) == (A_SPEECH_ORIGIN, A_SPEECH_SCOPE, A_SPEECH_VOICE)


@pytest.mark.parametrize("variable", _VARIABLES)
def test_a_missing_speech_value_refuses_the_start_naming_the_field(
    environment: pytest.MonkeyPatch, variable: str
) -> None:
    """S1: none of the three has a default."""
    environment.delenv(variable)

    with pytest.raises(ConfigurationError, match=re.escape(variable[13:].lower())):
        load_settings()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param(
            "PRESENTATOR_SPEECH_SERVICE", "http://speech.test/v1-MARKER", id="path"
        ),
        pytest.param(
            "PRESENTATOR_SPEECH_SERVICE",
            "http://u:p-MARKER@speech.test",
            id="credentials",
        ),
        pytest.param(
            "PRESENTATOR_SPEECH_SERVICE", "http://speech.test?q=MARKER", id="query"
        ),
        pytest.param("PRESENTATOR_SPEECH_SERVICE_SCOPE", "two MARKER", id="space"),
        pytest.param("PRESENTATOR_SPEECH_SERVICE_SCOPE", 'quote"MARKER', id="quote"),
        pytest.param(
            "PRESENTATOR_SPEECH_SERVICE_SCOPE", "back\\\\MARKER", id="backslash"
        ),
        pytest.param("PRESENTATOR_SPEECH_VOICE", "", id="empty-voice"),
    ],
)
def test_a_bad_speech_value_refuses_the_start_without_echoing_it(
    environment: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """S1: the field is named and the value is not quoted."""
    environment.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=variable[13:].lower()) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)


def test_a_dot_env_with_the_three_keys_starts_the_instance(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """S2: the three keys come from the file, not from the environment."""
    lines = [f"{variable}={os.environ[variable]}" for variable in _VARIABLES]
    for variable in _VARIABLES:
        environment.delenv(variable)
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")

    assert load_settings().speech_voice == A_SPEECH_VOICE


@pytest.mark.usefixtures("environment")
def test_a_leftover_speech_socket_key_in_a_dot_env_refuses_the_start(
    tmp_path: Path,
) -> None:
    """S2: the retired name is an unknown key like any other."""
    (tmp_path / ".env").write_text(
        "PRESENTATOR_SPEECH_SOCKET=/run/speech.sock\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="speech_socket"):
        load_settings()
