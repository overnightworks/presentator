"""What the compose file promises about the instance and its build worker.

The instance builds through the sealed worker and carries no authority over
this machine's Docker, and the worker keeps every key that seals it. Nothing
here starts a container; it reads the one file that must agree with itself.
"""

from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

_COMPOSE = Path(__file__).parents[1] / "compose.yaml"


def test_the_file_publishes_nothing_and_binds_nothing_outside_the_release() -> None:
    """S9e (#527): no ports, no env_file, no bind outside the release."""
    compose = _parsed_compose()

    for service in compose["services"].values():
        assert "ports" not in service
        assert "env_file" not in service
        assert service.get("network_mode") != "host"
        for volume in service.get("volumes", []):
            source = volume if isinstance(volume, str) else volume["source"]
            assert source.partition(":")[0] in compose["volumes"]


def test_no_environment_value_reads_the_callers_shell() -> None:
    """S9e (#527): plain values are literals, no `$` in any environment value."""
    for service in _parsed_compose()["services"].values():
        assert all(
            "$" not in (value or "") for value in _environment_of(service).values()
        )
    assert "${" not in _COMPOSE.read_text(encoding="utf-8").replace("${HOME}", "")


def test_no_healthcheck_reads_the_callers_shell() -> None:
    """S9e (#527): no `$` in any part of a service's healthcheck command."""
    for service in _parsed_compose()["services"].values():
        assert all(
            "$" not in part for part in service.get("healthcheck", {}).get("test", [])
        )


def test_every_service_tmpfs_entry_is_nosuid_and_nodev() -> None:
    """Hardening: every `tmpfs:` entry of a service sets nosuid and nodev."""
    mounts = [
        mount
        for service in _parsed_compose()["services"].values()
        for mount in service.get("tmpfs", [])
    ]

    assert mounts
    for mount in mounts:
        options = mount.partition(":")[2].split(",")
        assert {"nosuid", "nodev"} <= set(options)


def test_the_worker_joins_no_network_of_the_file() -> None:
    """S9e (#527): networks, only the instance is on one."""
    assert "networks" not in _parsed_compose()["services"]["build-worker"]


def test_the_worker_carries_no_label_the_proxy_could_route_by() -> None:
    """S9e (#527): labels, the proxy cannot route to the worker."""
    assert "labels" not in _parsed_compose()["services"]["build-worker"]


def _service_lines(name: str) -> list[str]:
    """The lines of one service, comments dropped, from its name to the next."""
    lines = _COMPOSE.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:") + 1
    end = next(
        (
            index
            for index in range(start, len(lines))
            if lines[index] and not lines[index].startswith("   ")
        ),
        len(lines),
    )
    return [line for line in lines[start:end] if not line.strip().startswith("#")]


@pytest.mark.parametrize(
    "sealing_line",
    [
        '    user: "0:presentator"',
        "    cap_drop:\n      - ALL",
        "    cap_add:\n      - SETUID\n      - SETGID\n      - KILL",
        "    security_opt:\n      - no-new-privileges",
        "    read_only: true",
        "    network_mode: none",
        "    ipc: private",
        "    pull_policy: never",
        "        volume:\n          nocopy: true",
    ],
)
def test_the_worker_service_carries_each_key_that_seals_it(sealing_line: str) -> None:
    """53 (#431): the sealed worker service in compose."""
    assert sealing_line in "\n".join(_service_lines("build-worker"))


def test_the_worker_swap_equals_its_memory_so_swap_cannot_double_the_bound() -> None:
    """53 (#431): the sealed worker service in compose."""
    keys = {
        key: value
        for key, _, value in (
            line.strip().partition(": ") for line in _service_lines("build-worker")
        )
    }

    assert keys["memswap_limit"] == keys["mem_limit"]


@pytest.mark.parametrize("absent_key", ["env_file", "init", "healthcheck", "build"])
def test_the_worker_service_has_no_key_that_would_unseal_or_rebuild_it(
    absent_key: str,
) -> None:
    """53 (#431): the sealed worker service in compose."""
    keys = {line.strip().partition(":")[0] for line in _service_lines("build-worker")}

    assert absent_key not in keys


def test_the_socket_volume_starts_as_a_private_root_directory() -> None:
    """53 (#431): the sealed worker service in compose."""
    written = _COMPOSE.read_text(encoding="utf-8")

    assert "mode=0750,uid=0" in written


@pytest.mark.parametrize(
    "hardening_line",
    [
        "        volume:\n          nocopy: true",
        "    cap_drop:\n      - ALL",
        "    security_opt:\n      - no-new-privileges:true",
        "    read_only: true",
    ],
)
def test_the_instance_runs_hardened(hardening_line: str) -> None:
    """B16: the web's flipped and hardened service in compose."""
    assert hardening_line in "\n".join(_service_lines("presentator"))


@pytest.mark.parametrize(
    "authority",
    ["docker.sock", "group_add", "DOCKER_GROUP"],
)
def test_the_instance_holds_no_authority_over_this_machines_docker(
    authority: str,
) -> None:
    """B16: no Docker socket and no docker group in the file."""
    assert authority not in _COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "setting"),
    [
        (
            "presentator_secret_key",
            "PRESENTATOR_SECRET_KEY_FILE",
        ),
        (
            "oidc_client_secret",
            "PRESENTATOR_OIDC_CLIENT_SECRET_FILE",
        ),
    ],
    ids=["master key", "client secret"],
)
def test_each_secret_is_a_file_secret_that_only_the_instance_mounts(
    name: str, setting: str
) -> None:
    """edge#280: every app secret reaches the container as a mounted file."""
    compose = _parsed_compose()

    assert set(compose["secrets"][name]) == {"file"}
    assert _secret_holders(compose, name) == ["presentator"]
    assert _environment_of(compose["services"]["presentator"])[setting] == (
        f"/run/secrets/{name}"
    )
    assert setting not in _environment_of(compose["services"]["build-worker"])
    assert "secrets" not in compose["services"]["build-worker"]


@pytest.mark.parametrize(
    "grant",
    ["presentator_secret_key", {"source": "presentator_secret_key"}],
    ids=["short form", "long form"],
)
def test_a_secret_granted_to_the_worker_in_either_form_is_caught(
    grant: str | dict[str, str],
) -> None:
    """#509 acceptance 2: a worker secret is caught in short and long form."""
    compose = _parsed_compose()
    compose["services"]["build-worker"]["secrets"] = [grant]

    assert _secret_holders(compose, "presentator_secret_key") == [
        "presentator",
        "build-worker",
    ]


def test_no_service_carries_the_master_key_in_its_environment() -> None:
    """#509: no PRESENTATOR_SECRET_KEY environment entry remains, in either form."""
    for service in _parsed_compose()["services"].values():
        assert "PRESENTATOR_SECRET_KEY" not in _environment_of(service)
        assert "args" not in service.get("build", {})
    assert "${PRESENTATOR_SECRET_KEY}" not in _COMPOSE.read_text(encoding="utf-8")


def _secret_holders(compose: dict[str, Any], name: str) -> list[str]:
    """Services granted a secret, whether the grant is short or long form."""
    return [
        service
        for service, body in compose["services"].items()
        if name
        in (
            entry if isinstance(entry, str) else entry["source"]
            for entry in body.get("secrets", [])
        )
    ]


def _parsed_compose() -> dict[str, Any]:
    document: dict[str, Any] = YAML(typ="safe").load(  # pyright: ignore[reportUnknownMemberType]
        _COMPOSE.read_text(encoding="utf-8")
    )
    return document


def _environment_of(service: dict[str, Any]) -> dict[str, str | None]:
    """A service's environment as a map, whether written as a map or a list."""
    environment: dict[str, str | None] | list[str] = service.get("environment", {})
    if isinstance(environment, dict):
        return environment
    return {
        name: value or None
        for name, _, value in (entry.partition("=") for entry in environment)
    }
