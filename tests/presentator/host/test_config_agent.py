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
    """CF5: none of the four has a default."""
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
    """CF5 for the backend cases: a bad agent value names the field, not the value."""
    environment.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=re.escape(field)) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)


def test_a_dot_env_with_the_four_keys_starts_the_instance(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The four keys come from the file, not from the environment."""
    lines = [f"{variable}={os.environ[variable]}" for variable in _FOUR_SETTINGS]
    for variable in _FOUR_SETTINGS:
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


_MCP_FIELD_OF = {
    "PRESENTATOR_AGENT__MCP__CLIENT_ID": "agent.mcp.client_id",
    "PRESENTATOR_AGENT__MCP__GROUP": "agent.mcp.group",
    "PRESENTATOR_AGENT__MCP__SCOPE": "agent.mcp.scope",
    "PRESENTATOR_AGENT__MCP__AGENT_GROUP": "agent.mcp.agent_group",
}


@pytest.fixture
def mcp_block() -> dict[str, str]:
    """A whole MCP block: each variable, by name, with a value that is valid."""
    return dict(
        zip(
            _MCP_FIELD_OF,
            ("an-agents-client", "/apps/presentator/agents", "mcp:tools", "/agents"),
            strict=True,
        )
    )


@pytest.mark.usefixtures("environment")
def test_without_the_mcp_variables_the_settings_name_no_mcp_block() -> None:
    assert load_settings().agent.mcp is None


def test_a_whole_mcp_block_names_the_client_its_groups_and_its_scope(
    environment: pytest.MonkeyPatch, mcp_block: dict[str, str]
) -> None:
    for variable, value in mcp_block.items():
        environment.setenv(variable, value)

    mcp = load_settings().agent.mcp

    assert mcp is not None
    assert (mcp.client_id, mcp.group, mcp.scope, mcp.agent_group) == tuple(
        mcp_block.values()
    )


@pytest.mark.parametrize(("missing", "field"), _MCP_FIELD_OF.items())
def test_a_partial_mcp_block_refuses_the_start_naming_the_field(
    environment: pytest.MonkeyPatch,
    mcp_block: dict[str, str],
    missing: str,
    field: str,
) -> None:
    for variable, value in mcp_block.items():
        if variable != missing:
            environment.setenv(variable, value)

    with pytest.raises(ConfigurationError, match=re.escape(field)):
        load_settings()


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        pytest.param("PRESENTATOR_AGENT__MCP__CLIENT_ID", "", id="empty client id"),
        pytest.param(
            "PRESENTATOR_AGENT__MCP__GROUP", "no-slash-MARKER", id="a group no path"
        ),
        pytest.param(
            "PRESENTATOR_AGENT__MCP__GROUP", "/apps//MARKER", id="an empty segment"
        ),
        pytest.param(
            "PRESENTATOR_AGENT__MCP__SCOPE", "two MARKER", id="two scope tokens"
        ),
        pytest.param("PRESENTATOR_AGENT__MCP__SCOPE", "", id="no scope"),
        pytest.param(
            "PRESENTATOR_AGENT__MCP__AGENT_GROUP",
            "no-slash-MARKER",
            id="an agent group no path",
        ),
        pytest.param(
            "PRESENTATOR_AGENT__MCP__AGENT_GROUP", "", id="an empty agent group"
        ),
    ],
)
def test_a_malformed_mcp_block_refuses_the_start_without_echoing_it(
    environment: pytest.MonkeyPatch,
    mcp_block: dict[str, str],
    variable: str,
    value: str,
) -> None:
    for name, given in mcp_block.items():
        environment.setenv(name, given)
    environment.setenv(variable, value)

    with pytest.raises(
        ConfigurationError, match=re.escape(_MCP_FIELD_OF[variable])
    ) as refused:
        load_settings()

    assert "MARKER" not in str(refused.value)
