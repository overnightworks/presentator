"""The agent service's scope: required, and one RFC 6749 scope token."""

import pytest

from presentator.host.config import ConfigurationError, load_settings

_SETTING = "PRESENTATOR_AGENT_SERVICE_SCOPE"


def test_an_instance_without_the_agent_service_scope_refuses_to_start(
    environment: pytest.MonkeyPatch,
) -> None:
    """Cell 4 (#673)."""
    environment.delenv(_SETTING)

    with pytest.raises(ConfigurationError, match="agent_service_scope"):
        load_settings()


@pytest.mark.parametrize("scope", ["agent-service-audience", "a:b/c.d~e"])
def test_the_agent_service_scope_takes_one_scope_token(
    environment: pytest.MonkeyPatch, scope: str
) -> None:
    """Cell 4 (#673)."""
    environment.setenv(_SETTING, scope)

    assert load_settings().agent_service_scope == scope


@pytest.mark.parametrize(
    "scope",
    ["two MARKER scopes", "MARKER ", 'quote"MARKER', "back\\slashMARKER"],
)
def test_the_agent_service_scope_refuses_anything_but_one_token_without_quoting_it(
    environment: pytest.MonkeyPatch, scope: str
) -> None:
    """Cell 5 (#673)."""
    environment.setenv(_SETTING, scope)

    with pytest.raises(ConfigurationError, match="agent_service_scope") as refused:
        load_settings()

    assert scope not in str(refused.value)


def test_the_agent_service_scope_refuses_an_empty_value(
    environment: pytest.MonkeyPatch,
) -> None:
    """Cell 5 (#673)."""
    environment.setenv(_SETTING, "")

    with pytest.raises(ConfigurationError, match="agent_service_scope"):
        load_settings()
