"""The realm's names come from the nine `REALM_` keys, and only from them."""

from pathlib import Path

import pytest

from presentator.host import main
from presentator.host.config import load_settings
from presentator.host.environment import ConfigurationError
from tests.support.lobby import A_REALM

# The keys are the platform's contract, so they are written out here.
_REALM_KEYS = (
    "REALM_CLIENT_ID",
    "REALM_LOGIN_GROUP",
    "REALM_ADMIN_GROUP",
    "REALM_MCP_GROUP",
    "REALM_MCP_SCOPE",
    "REALM_AGENT_GROUP",
    "REALM_MCP_CLIENT_ID",
    "REALM_AI_GROUP",
    "REALM_SPEECH_SCOPE",
)


def _refusal() -> ConfigurationError:
    with pytest.raises(ConfigurationError) as refused:
        load_settings()
    return refused.value


def _write_a_dot_env(directory: Path, entries: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in entries.items()]
    (directory / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_realm_is_read_from_its_nine_keys(
    environment: pytest.MonkeyPatch,
) -> None:
    del environment

    assert load_settings().realm == A_REALM


def test_a_nested_presentator_realm_variable_never_sets_the_realm(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PRESENTATOR_REALM__LOGIN_GROUP", "/apps/nested")
    environment.setenv("PRESENTATOR_REALM__CLIENT_ID", "nested-client")

    assert load_settings().realm == A_REALM

    for key in _REALM_KEYS:
        environment.delenv(key)
    assert set(_refusal().variables) == set(_REALM_KEYS)


@pytest.mark.parametrize("key", _REALM_KEYS)
def test_a_missing_realm_key_refuses_naming_only_it(
    environment: pytest.MonkeyPatch, key: str
) -> None:
    environment.delenv(key)

    assert _refusal().variables == (key,)


@pytest.mark.parametrize("key", _REALM_KEYS)
def test_an_empty_realm_key_refuses_naming_only_it(
    environment: pytest.MonkeyPatch, key: str
) -> None:
    environment.setenv(key, "")

    assert _refusal().variables == (key,)


@pytest.mark.parametrize(
    "key",
    [
        "REALM_LOGIN_GROUP",
        "REALM_ADMIN_GROUP",
        "REALM_MCP_GROUP",
        "REALM_AGENT_GROUP",
        "REALM_AI_GROUP",
    ],
)
@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param("apps/presentator", id="no leading slash"),
        pytest.param("/apps//presentator", id="an empty segment"),
        pytest.param("/apps/ presentator", id="whitespace around a segment"),
        pytest.param("/apps/presen\ttator", id="a control character"),
    ],
)
def test_a_malformed_realm_group_refuses_naming_only_its_key_and_never_the_value(
    environment: pytest.MonkeyPatch, key: str, malformed: str
) -> None:
    environment.setenv(key, malformed)

    refused = _refusal()

    assert refused.variables == (key,)
    assert malformed not in str(refused)


@pytest.mark.parametrize("key", ["REALM_MCP_SCOPE", "REALM_SPEECH_SCOPE"])
@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param("two scopes", id="a space"),
        pytest.param('"quoted"', id="a quote"),
        pytest.param("a\\backslash", id="a backslash"),
    ],
)
def test_a_malformed_realm_scope_refuses_naming_only_its_key_and_never_the_value(
    environment: pytest.MonkeyPatch, key: str, malformed: str
) -> None:
    environment.setenv(key, malformed)

    refused = _refusal()

    assert refused.variables == (key,)
    assert malformed not in str(refused)


@pytest.mark.parametrize(
    "admin_group",
    [
        pytest.param("/apps/presentator", id="the login group itself"),
        pytest.param("/apps/presentator2", id="a sibling sharing a prefix"),
        pytest.param("/other/admin", id="outside"),
        pytest.param("/apps", id="above"),
    ],
)
def test_an_admin_group_not_strictly_below_the_login_group_refuses_naming_the_admin_key(
    environment: pytest.MonkeyPatch, admin_group: str
) -> None:
    environment.setenv("REALM_ADMIN_GROUP", admin_group)

    refused = _refusal()

    assert refused.variables == ("REALM_ADMIN_GROUP",)
    assert admin_group not in str(refused)


def test_a_realm_fault_and_a_settings_fault_of_one_start_are_named_together(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("REALM_LOGIN_GROUP", "no-slash")
    environment.delenv("PRESENTATOR_S3_BUCKET")

    assert _refusal().variables == ("PRESENTATOR_S3_BUCKET", "REALM_LOGIN_GROUP")


def test_a_dot_env_holds_the_realm_and_the_settings_and_the_environment_wins(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for key in _REALM_KEYS:
        environment.delenv(key)
    environment.delenv("PRESENTATOR_S3_BUCKET")
    entries = {
        "REALM_CLIENT_ID": "client-of-the-file",
        "REALM_LOGIN_GROUP": "/apps/file",
        "REALM_ADMIN_GROUP": "/apps/file/admin",
        "REALM_MCP_GROUP": "/apps/file/mcp",
        "REALM_MCP_SCOPE": "scope-of-the-file",
        "REALM_AGENT_GROUP": "/agents-of-the-file",
        "REALM_MCP_CLIENT_ID": "mcp-client-of-the-file",
        "REALM_AI_GROUP": "/ai-of-the-file",
        "REALM_SPEECH_SCOPE": "speech-scope-of-the-file",
        "PRESENTATOR_S3_BUCKET": "bucket-of-the-file",
    }
    _write_a_dot_env(tmp_path, entries)
    environment.setenv("REALM_CLIENT_ID", "client-of-the-environment")

    settings = load_settings()

    assert settings.s3_bucket == "bucket-of-the-file"
    assert settings.realm.client_id == "client-of-the-environment"
    assert str(settings.realm.login_group) == "/apps/file"
    assert str(settings.realm.admin_group) == "/apps/file/admin"
    assert str(settings.realm.mcp_group) == "/apps/file/mcp"
    assert settings.realm.mcp_scope == "scope-of-the-file"
    assert str(settings.realm.agent_group) == "/agents-of-the-file"
    assert settings.realm.mcp_client_id == "mcp-client-of-the-file"
    assert str(settings.realm.ai_group) == "/ai-of-the-file"
    assert settings.realm.speech_scope == "speech-scope-of-the-file"


def test_an_unknown_realm_key_in_a_dot_env_refuses_naming_it(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del environment
    _write_a_dot_env(tmp_path, {"REALM_UNKNOWN_KEY_FOR_TEST": "1"})

    assert _refusal().variables == ("REALM_UNKNOWN_KEY_FOR_TEST",)


def test_the_retired_client_id_key_in_a_dot_env_refuses_and_is_named(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del environment
    (tmp_path / ".env").write_text("PRESENTATOR_OIDC_CLIENT_ID=presentator\n")

    assert _refusal().variables == ("PRESENTATOR_OIDC_CLIENT_ID",)


def test_the_retired_client_id_key_in_the_process_environment_is_ignored(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PRESENTATOR_OIDC_CLIENT_ID", "left-over-from-before")

    assert load_settings().realm == A_REALM


def test_the_retired_speech_scope_alone_does_not_start_the_instance(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.delenv("REALM_SPEECH_SCOPE")
    environment.setenv("PRESENTATOR_SPEECH_SERVICE_SCOPE", "/left-over/or-scope")

    assert _refusal().variables == ("REALM_SPEECH_SCOPE",)


def test_the_retired_speech_scope_in_the_process_environment_is_ignored(
    environment: pytest.MonkeyPatch,
) -> None:
    environment.setenv("PRESENTATOR_SPEECH_SERVICE_SCOPE", "left-over-scope")

    assert load_settings().realm.speech_scope == A_REALM.speech_scope


def test_the_retired_speech_scope_in_a_dot_env_refuses_and_is_named(
    environment: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    del environment
    _write_a_dot_env(tmp_path, {"PRESENTATOR_SPEECH_SERVICE_SCOPE": "left-over"})

    assert _refusal().variables == ("PRESENTATOR_SPEECH_SERVICE_SCOPE",)


@pytest.mark.usefixtures("served_database")
def test_migrate_needs_no_realm_key(environment: pytest.MonkeyPatch) -> None:
    for key in _REALM_KEYS:
        environment.delenv(key)

    main.main(["migrate"])
