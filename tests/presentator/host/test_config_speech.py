"""The three speech settings: required, canonical, never echoed."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from speech_client import wire

from presentator.contracts.voice import SampleLanguage
from presentator.host.config import ConfigurationError, Settings, load_settings
from presentator.host.main import build_instance
from tests.support.lobby import A_SPEECH_SCOPE, A_SPEECH_SERVICE, a_settings

if TYPE_CHECKING:
    from pathlib import Path

A_CREDENTIALED_ORIGIN = "http://user:pa55word-MARKER@speech:8080"
_REMOVED_VOICE_VARIABLE = "PRESENTATOR_SPEECH_" + "VOICE"
_FIELDS = ("speech_service", "speech_service_scope")


def _settings_with(**overrides: object) -> Settings:
    good = a_settings().model_dump()
    return Settings.model_validate({**good, **overrides})


def _voice_field(language: SampleLanguage) -> str:
    return f"speech_voice_{language.value}"


def test_the_service_and_its_scope_build_settings_without_any_network() -> None:
    settings = _settings_with()

    assert settings.speech_service == A_SPEECH_SERVICE
    assert settings.speech_service_scope == A_SPEECH_SCOPE


def test_each_language_has_a_voice_that_speaks_it_when_no_setting_names_one() -> None:
    voices = a_settings().speech_voices()

    assert set(voices) == set(SampleLanguage)
    for language, voice in voices.items():
        assert wire.Language(language.value) in wire.VOICES[voice].languages


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


@pytest.mark.parametrize("language", list(SampleLanguage))
@pytest.mark.parametrize("voice", ["", "a-voice-outside-the-catalogue"])
def test_a_voice_outside_the_catalogue_refuses_naming_the_field(
    language: SampleLanguage, voice: str
) -> None:
    with pytest.raises(ValidationError) as refused:
        _settings_with(**{_voice_field(language): voice})

    assert _voice_field(language) in str(refused.value)


@pytest.mark.parametrize(
    ("language", "voice"),
    [
        (language, voice)
        for language in SampleLanguage
        for voice, entry in wire.VOICES.items()
        if wire.Language(language.value) not in entry.languages
    ],
)
def test_a_voice_that_does_not_speak_the_settings_language_refuses_naming_it(
    language: SampleLanguage, voice: wire.VoiceId
) -> None:
    with pytest.raises(ValidationError) as refused:
        _settings_with(**{_voice_field(language): voice})

    assert _voice_field(language) in str(refused.value)


@pytest.mark.parametrize("language", list(SampleLanguage))
def test_a_voice_of_several_languages_is_accepted_for_either_language(
    language: SampleLanguage,
) -> None:
    settings = _settings_with(**{_voice_field(language): wire.VoiceId.RYAN})

    assert settings.speech_voices()[language] == wire.VoiceId.RYAN


def test_an_env_file_names_the_voices_and_a_leftover_key_refuses(
    tmp_path: Path, environment: pytest.MonkeyPatch
) -> None:
    environment.chdir(tmp_path)
    for name in ("SERVICE", "SERVICE_SCOPE"):
        environment.delenv(f"PRESENTATOR_SPEECH_{name}")
    env_file = tmp_path / ".env"
    lines = [
        f"PRESENTATOR_SPEECH_SERVICE={A_SPEECH_SERVICE}",
        f"PRESENTATOR_SPEECH_SERVICE_SCOPE={A_SPEECH_SCOPE}",
        f"PRESENTATOR_SPEECH_VOICE_EN={wire.VoiceId.RYAN}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert load_settings().speech_voices()[SampleLanguage.ENGLISH] == wire.VoiceId.RYAN

    env_file.write_text(f"{_REMOVED_VOICE_VARIABLE}=ryan\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as refused:
        load_settings()
    assert _REMOVED_VOICE_VARIABLE.lower() in str(refused.value).lower()


def test_the_removed_voice_variable_in_the_process_environment_is_ignored(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv(_REMOVED_VOICE_VARIABLE, "a-voice-outside-the-catalogue")

    assert load_settings().speech_voices() == a_settings().speech_voices()


@pytest.mark.usefixtures("environment")
@pytest.mark.parametrize("transport", ["httpx2", "httpcore2.http11"])
def test_the_composed_instance_drops_the_transports_traces_below_a_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    transport: str,
) -> None:
    logger = logging.getLogger(transport)
    monkeypatch.setattr(logger, "filters", [])

    build_instance(load_settings())
    with caplog.at_level(logging.DEBUG, logger=transport):
        logger.debug("a-debug-trace-MARKER")
        logger.info("an-info-trace-MARKER")

    assert "MARKER" not in caplog.text
