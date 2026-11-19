"""The three speech settings: required, canonical, never echoed."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from speech_client import wire

from presentator.contracts.words import LANGUAGES, Language
from presentator.host import main
from presentator.host.config import Settings, load_settings
from presentator.host.environment import ConfigurationError
from tests.support.lobby import A_SPEECH_SCOPE, A_SPEECH_SERVICE, a_settings
from tests.support.log_lines import json_lines

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

A_CREDENTIALED_ORIGIN = "http://user:pa55word-MARKER@speech:8080"
# The per-language form `PRESENTATOR_SPEECH_VOICES__EN` is read; this one is not.
_UNREAD_VOICE_VARIABLE = "PRESENTATOR_SPEECH_VOICE_EN"
_FIELDS = ("speech_service", "speech_service_scope")


def _settings_with(**overrides: object) -> Settings:
    good = a_settings().model_dump()
    return Settings.model_validate({**good, **overrides})


def _voice_variable(language: Language) -> str:
    return f"PRESENTATOR_SPEECH_VOICES__{language.upper()}"


def test_the_service_and_its_scope_build_settings_without_any_network() -> None:
    settings = _settings_with()

    assert settings.speech_service == A_SPEECH_SERVICE
    assert settings.speech_service_scope == A_SPEECH_SCOPE


def test_each_language_has_a_voice_that_speaks_it_when_no_setting_names_one() -> None:
    voices = a_settings().speech_voices

    assert set(voices) == set(LANGUAGES)
    for language, voice in voices.items():
        assert wire.Language(language) in wire.VOICES[voice].languages


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


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("voice", ["", "a-voice-outside-the-catalogue"])
def test_a_voice_outside_the_catalogue_refuses_naming_its_variable(
    language: Language, voice: str, environment: pytest.MonkeyPatch
) -> None:
    environment.setenv(_voice_variable(language), voice)

    with pytest.raises(ConfigurationError) as refused:
        load_settings()

    assert refused.value.variables == (_voice_variable(language),)


@pytest.mark.parametrize(
    ("language", "voice"),
    [
        (language, voice)
        for language in LANGUAGES
        for voice, entry in wire.VOICES.items()
        if wire.Language(language) not in entry.languages
    ],
)
def test_a_voice_that_does_not_speak_its_language_refuses_naming_its_variable(
    language: Language, voice: wire.VoiceId, environment: pytest.MonkeyPatch
) -> None:
    environment.setenv(_voice_variable(language), voice)

    with pytest.raises(ConfigurationError) as refused:
        load_settings()

    assert refused.value.variables == (_voice_variable(language),)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("form", ["entry", "object"])
def test_a_voice_set_for_one_language_leaves_the_others_shipped_voice(
    language: Language, form: str, environment: pytest.MonkeyPatch
) -> None:
    if form == "entry":
        environment.setenv(_voice_variable(language), wire.VoiceId.RYAN)
    else:
        named = json.dumps({language: wire.VoiceId.RYAN})
        environment.setenv("PRESENTATOR_SPEECH_VOICES", named)

    voices = load_settings().speech_voices

    shipped = a_settings().speech_voices
    assert voices == {**shipped, language: wire.VoiceId.RYAN}


def test_an_env_file_names_a_voice_and_an_entry_no_setting_reads_refuses(
    tmp_path: Path, environment: pytest.MonkeyPatch
) -> None:
    environment.chdir(tmp_path)
    for name in ("SERVICE", "SERVICE_SCOPE"):
        environment.delenv(f"PRESENTATOR_SPEECH_{name}")
    env_file = tmp_path / ".env"
    lines = [
        f"PRESENTATOR_SPEECH_SERVICE={A_SPEECH_SERVICE}",
        f"PRESENTATOR_SPEECH_SERVICE_SCOPE={A_SPEECH_SCOPE}",
        f"{_voice_variable('en')}={wire.VoiceId.RYAN}",
    ]
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert load_settings().speech_voices["en"] == wire.VoiceId.RYAN

    env_file.write_text(f"{_UNREAD_VOICE_VARIABLE}=ryan\n", encoding="utf-8")
    with pytest.raises(ConfigurationError) as refused:
        load_settings()
    assert _UNREAD_VOICE_VARIABLE.lower() in str(refused.value).lower()


def test_a_voice_variable_no_setting_reads_in_the_process_environment_is_ignored(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv(_UNREAD_VOICE_VARIABLE, "a-voice-outside-the-catalogue")

    assert load_settings().speech_voices == a_settings().speech_voices


@pytest.mark.parametrize("transport", ["httpx2", "httpcore2.http11"])
def test_the_hosts_logging_drops_the_transports_traces_below_a_warning(
    main_without_serving: Callable[[], None],
    capsys: pytest.CaptureFixture[str],
    transport: str,
) -> None:
    main_without_serving()
    logger = logging.getLogger(transport)

    logger.debug("a-debug-trace-MARKER")
    logger.info("an-info-trace-MARKER")
    logger.warning("a-warning-trace")

    written = [
        line
        for line in json_lines(capsys.readouterr().out)
        if line["logger"] == transport
    ]
    assert [line["event"] for line in written] == ["a-warning-trace"]
    assert written[0]["service"] == main.SERVICE_NAME
