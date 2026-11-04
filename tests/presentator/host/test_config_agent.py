"""The `agent` section: four required values, read from the environment or a `.env`."""

import os
import re
from pathlib import Path

import pytest

from presentator.host.config import ConfigurationError, load_settings
from tests.support.lobby import A_GATEWAY_ORIGIN, AN_API_GROUP

_FIELD_OF = {
    "PRESENTATOR_AGENT__BACKEND": "agent.backend",
    "PRESENTATOR_AGENT__GATEWAY__ORIGIN": "agent.gateway.origin",
    "PRESENTATOR_AGENT__GATEWAY__KEY_FILE": "agent.gateway.key_file",
    "PRESENTATOR_AGENT__API_GROUP": "agent.api_group",
}
_FOUR_SETTINGS = tuple(_FIELD_OF)


def test_the_four_agent_values_start_the_instance_and_read_no_key(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C1: the key file is read per question, so an absent one starts."""
    environment.setenv(
        "PRESENTATOR_AGENT__GATEWAY__KEY_FILE", str(tmp_path / "not-written-yet")
    )

    settings = load_settings()

    assert settings.agent.gateway.origin == A_GATEWAY_ORIGIN
    assert settings.agent.api_group == AN_API_GROUP


@pytest.mark.parametrize(("variable", "field"), _FIELD_OF.items())
def test_a_missing_agent_value_refuses_the_start_naming_the_field(
    environment: pytest.MonkeyPatch, variable: str, field: str
) -> None:
    """C1: none of the four has a default."""
    environment.delenv(variable)

    with pytest.raises(ConfigurationError, match=re.escape(field)):
        load_settings()


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
        pytest.param(
            "PRESENTATOR_AGENT__BACKEND", "service", "agent.backend", id="service"
        ),
        pytest.param(
            "PRESENTATOR_AGENT__BACKEND",
            "other-MARKER",
            "agent.backend",
            id="other-backend",
        ),
        pytest.param(
            "PRESENTATOR_AGENT__GATEWAY__ORIGIN",
            "http://gateway.test:4000/v1?token=secret-MARKER",
            "agent.gateway.origin",
            id="origin-with-a-path",
        ),
        pytest.param(
            "PRESENTATOR_AGENT__GATEWAY__ORIGIN",
            "https://gateway.test",
            "agent.gateway",
            id="https-without-a-ca-file",
        ),
        pytest.param(
            "PRESENTATOR_AGENT__GATEWAY__KEY_FILE",
            "relative-MARKER/key",
            "agent.gateway.key_file",
            id="relative-key-file",
        ),
    ],
)
def test_a_bad_agent_value_refuses_the_start_without_echoing_it(
    environment: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    """C1: the field is named and the value is not quoted."""
    environment.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=re.escape(field)) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)


def test_a_dot_env_with_the_four_keys_starts_the_instance(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C1b: the four keys come from the file, not from the environment."""
    lines = [f"{variable}={os.environ[variable]}" for variable in _FOUR_SETTINGS]
    for variable in _FOUR_SETTINGS:
        environment.delenv(variable)
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")

    assert load_settings().agent.api_group == AN_API_GROUP


@pytest.mark.usefixtures("environment")
def test_a_leftover_agent_service_key_in_a_dot_env_refuses_the_start(
    tmp_path: Path,
) -> None:
    """C1b: the retired name is an unknown key like any other."""
    (tmp_path / ".env").write_text(
        "PRESENTATOR_AGENT_SERVICE=http://agent-service:8080\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="agent_service"):
        load_settings()
