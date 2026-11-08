"""The three speech settings: required, canonical, never echoed."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from presentator.host.config import ConfigurationError, Settings, load_settings
from tests.support.lobby import (
    A_SPEECH_SCOPE,
    A_SPEECH_SERVICE,
    A_SPEECH_VOICE,
    a_settings,
)

if TYPE_CHECKING:
    from pathlib import Path

A_CREDENTIALED_ORIGIN = "http://user:pa55word-MARKER@speech:8080"
_FIELDS = ("speech_service", "speech_service_scope", "speech_voice")


def _settings_with(**overrides: object) -> Settings:
    good = a_settings().model_dump()
    return Settings.model_validate({**good, **overrides})


def test_the_three_values_build_settings_without_any_network() -> None:
    settings = _settings_with()

    assert settings.speech_service == A_SPEECH_SERVICE
    assert settings.speech_service_scope == A_SPEECH_SCOPE
    assert settings.speech_voice == A_SPEECH_VOICE


@pytest.mark.parametrize("name", _FIELDS)
def test_each_missing_value_refuses_naming_the_field(name: str) -> None:
    good = a_settings().model_dump()
    del good[name]

    with pytest.raises(ValidationError) as refused:
        Settings.model_validate(good)

    assert name in str(refused.value)


@pytest.mark.parametrize(
    "origin",
    [
        "https://speech:8080",
        "http://speech:8080/path",
        "http://speech:8080/",
        "http://speech:8080?query=1",
        A_CREDENTIALED_ORIGIN,
        "ftp://speech:8080",
        "not an origin",
    ],
)
def test_an_origin_that_is_not_one_canonical_http_origin_refuses_without_echoing_it(
    origin: str,
) -> None:
    with pytest.raises(ValidationError) as refused:
        _settings_with(speech_service=origin)

    assert origin not in str(
        refused.value.errors(include_input=False, include_context=False)
    )
    assert "speech_service" in str(refused.value)


@pytest.mark.parametrize("scope", ["two scopes", 'a"quote', "a\\backslash", ""])
def test_a_scope_that_is_not_one_token_refuses_without_echoing_it(scope: str) -> None:
    with pytest.raises(ValidationError) as refused:
        _settings_with(speech_service_scope=scope)

    assert "speech_service_scope" in str(refused.value)
    errors = refused.value.errors(include_input=False, include_context=False)
    assert all(scope not in str(error["msg"]) for error in errors if scope)


def test_an_empty_voice_refuses() -> None:
    with pytest.raises(ValidationError):
        _settings_with(speech_voice="")


def test_an_env_file_with_the_three_keys_starts_and_a_leftover_socket_key_refuses(
    tmp_path: Path, environment: pytest.MonkeyPatch
) -> None:
    environment.chdir(tmp_path)
    for name in ("SERVICE", "SERVICE_SCOPE", "VOICE"):
        environment.delenv(f"PRESENTATOR_SPEECH_{name}")
    env_file = tmp_path / ".env"
    lines = [
        f"PRESENTATOR_SPEECH_SERVICE={A_SPEECH_SERVICE}",
        f"PRESENTATOR_SPEECH_SERVICE_SCOPE={A_SPEECH_SCOPE}",
        f"PRESENTATOR_SPEECH_VOICE={A_SPEECH_VOICE}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert load_settings().speech_voice == A_SPEECH_VOICE

    leftover = "SPEECH_" + "SOCKET"
    env_file.write_text(f"PRESENTATOR_{leftover}=/run/old\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as refused:
        load_settings()
    assert leftover.lower() in str(refused.value).lower()
