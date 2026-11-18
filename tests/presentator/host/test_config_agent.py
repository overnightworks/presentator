"""The `agent` section: own settings, a stale variable refused, no MCP setting."""

import os
import re
from http import HTTPStatus
from pathlib import Path

import pytest
from agents.mcp_wire import MCP_ENDPOINT_PATH
from fastapi.testclient import TestClient

from presentator.host import main
from presentator.host.config import ConfigurationError, load_settings
from tests.presentator.host.support.config_refusals import assert_refusal_names
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_GATEWAY_ORIGIN,
    AN_API_GROUP,
)

pytestmark = pytest.mark.usefixtures("redis_environment")

_FIELD_OF = {
    "PRESENTATOR_AGENT__GATEWAY__ORIGIN": "agent.gateway.origin",
    "PRESENTATOR_AGENT__GATEWAY__KEY_FILE": "agent.gateway.key_file",
    "PRESENTATOR_AGENT__API_GROUP": "agent.api_group",
}
_THREE_SETTINGS = tuple(_FIELD_OF)


def test_the_three_agent_values_start_the_instance_and_read_no_key(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The key file is read per question, so an absent one starts."""
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
    """CF5: none of the three has a default."""
    environment.delenv(variable)

    assert_refusal_names(load_settings, field)


@pytest.mark.parametrize(
    ("variable", "value", "field"),
    [
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
        pytest.param(
            "PRESENTATOR_AGENT",
            '["a-list-MARKER"]',
            "agent",
            id="section-not-an-object",
        ),
    ],
)
def test_a_bad_agent_value_refuses_the_start_without_echoing_it(
    environment: pytest.MonkeyPatch, variable: str, value: str, field: str
) -> None:
    """CF5: a bad agent value names the field, not the value."""
    environment.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=re.escape(field)) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)


def test_a_dot_env_with_the_three_keys_starts_the_instance(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The three keys come from the file, not from the environment."""
    lines = [f"{variable}={os.environ[variable]}" for variable in _THREE_SETTINGS]
    for variable in _THREE_SETTINGS:
        environment.delenv(variable)
    (tmp_path / ".env").write_text("\n".join(lines), encoding="utf-8")

    assert load_settings().agent.api_group == AN_API_GROUP


@pytest.mark.usefixtures("environment")
def test_an_unknown_presentator_key_in_a_dot_env_refuses_the_start(
    tmp_path: Path,
) -> None:
    """A `PRESENTATOR_` key no setting owns is refused, not ignored."""
    (tmp_path / ".env").write_text(
        "PRESENTATOR_UNKNOWN_KEY_FOR_TEST=1\n", encoding="utf-8"
    )

    with pytest.raises(ConfigurationError, match="unknown_key_for_test"):
        load_settings()


@pytest.mark.usefixtures("environment", "served_database")
def test_the_lobby_starts_with_no_mcp_setting_and_serves_the_front() -> None:
    instance = main.build_instance(load_settings())

    metadata = TestClient(
        instance.lobby, base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    ).get(f"/.well-known/oauth-protected-resource{MCP_ENDPOINT_PATH}")

    assert metadata.status_code == HTTPStatus.OK


def test_the_retired_backend_variable_starts_the_instance(
    environment: pytest.MonkeyPatch,
) -> None:
    """The previous release's deployment still sets it; its value is unread."""
    environment.setenv("PRESENTATOR_AGENT__BACKEND", "any-value-at-all")

    assert load_settings().agent.api_group == AN_API_GROUP


@pytest.mark.parametrize(
    ("variable", "field"),
    [
        pytest.param("PRESENTATOR_AGENT__MCP__GROUP", "agent.mcp", id="mcp"),
        pytest.param("PRESENTATOR_AGENT__BACKENDS", "agent.backends", id="other"),
    ],
)
def test_a_stale_agent_variable_refuses_the_start_naming_the_key_not_the_value(
    environment: pytest.MonkeyPatch, variable: str, field: str
) -> None:
    environment.setenv(variable, "a-stale-MARKER")

    with pytest.raises(ConfigurationError, match=re.escape(field)) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)
