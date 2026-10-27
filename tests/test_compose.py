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
    """The edge's proxy is the only way in; the release owns every mount."""
    compose = _parsed_compose()

    for service in compose["services"].values():
        assert "ports" not in service
        assert "env_file" not in service
        assert service.get("network_mode") != "host"
        for volume in service.get("volumes", []):
            source = volume if isinstance(volume, str) else volume["source"]
            assert source.partition(":")[0] in compose["volumes"]
    assert "presentator-speech" not in _COMPOSE.read_text(encoding="utf-8")


def test_no_environment_value_reads_the_callers_shell() -> None:
    """The edge runs Compose with the caller's whole environment (ADR 0019)."""
    instance = _environment_of(_parsed_compose()["services"]["presentator"])

    assert all("$" not in (value or "") for value in instance.values())
    assert "${" not in _COMPOSE.read_text(encoding="utf-8").replace("${HOME}", "")


def test_the_instance_reaches_the_agent_service_over_the_edges_network() -> None:
    """Cell 26."""
    compose = _parsed_compose()
    instance = compose["services"]["presentator"]

    assert instance["environment"]["PRESENTATOR_AGENT_SERVICE"] == (
        "http://agent-service:8080"
    )
    assert "agent" in instance["networks"]
    assert compose["networks"]["agent"] == {
        "external": True,
        "name": "hal_agent_presentator",
    }


def test_the_instance_joins_the_back_channel_under_its_public_hostname() -> None:
    """Keycloak reaches the logout route on the network the edge creates."""
    compose = _parsed_compose()
    instance = compose["services"]["presentator"]

    assert instance["networks"]["backchannel"] == {
        "aliases": ["presentator.hallucinai.de"]
    }
    assert compose["networks"]["backchannel"] == {
        "external": True,
        "name": "hal_backchannel_presentator",
    }
    assert "networks" not in compose["services"]["build-worker"]


def test_the_trusted_proxy_is_the_gateway_of_the_instances_own_network() -> None:
    """The edge's proxy reaches the instance as the network's gateway."""
    compose = _parsed_compose()
    network = compose["networks"]["default"]
    (pool,) = network["ipam"]["config"]

    assert network["name"] == "presentator_default"
    assert pool == {"subnet": "172.31.251.0/24", "gateway": "172.31.251.1"}
    assert (
        _environment_of(compose["services"]["presentator"])[
            "PRESENTATOR_TRUSTED_PROXIES"
        ]
        == pool["gateway"]
    )


def test_the_instance_is_routed_under_both_hosts_through_the_edge() -> None:
    labels = _parsed_compose()["services"]["presentator"]["labels"]

    assert labels == {
        "traefik.enable": "true",
        "edge.name": "presentator",
        "edge.alias": "presentator-talk",
        "traefik.http.services.presentator.loadbalancer.server.port": "8000",
        "traefik.docker.network": "presentator_default",
    }
    assert "labels" not in _parsed_compose()["services"]["build-worker"]


def test_the_instance_asks_its_own_health_route_with_the_public_host() -> None:
    check = _parsed_compose()["services"]["presentator"]["healthcheck"]["test"]
    script = check[-1]

    assert check[:3] == ["CMD", "python", "-c"]
    assert "http://127.0.0.1:8000/health" in script
    assert "os.environ['PRESENTATOR_PUBLIC_ORIGIN']" in script
    assert "$" not in script


@pytest.mark.parametrize("service", ["presentator", "build-worker"])
def test_each_service_restarts_unless_it_was_stopped(service: str) -> None:
    assert _parsed_compose()["services"][service]["restart"] == "unless-stopped"


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
        "    entrypoint: []",
        "    cap_drop:\n      - ALL",
        "    cap_add:\n      - SETUID\n      - SETGID\n      - KILL",
        "    security_opt:\n      - no-new-privileges",
        "    read_only: true",
        "    network_mode: none",
        "    ipc: private",
        "    shm_size: 256m",
        "    pids_limit: 512",
        "    restart: unless-stopped",
        "    stop_signal: SIGKILL",
        "    pull_policy: never",
        "      - /work:size=2g,nr_inodes=200k,mode=755,nosuid,nodev",
        "      - /tmp:size=512m,nr_inodes=10k,mode=1777,nosuid,nodev",
        "        source: build-socket\n        target: /run/presentator-build",
        "        volume:\n          nocopy: true",
        "      - PRESENTATOR_BUILD_TIMEOUT_SECONDS",
        "      - PRESENTATOR_BUILD_OUTPUT_MEGABYTES",
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

    assert keys["memswap_limit"] == keys["mem_limit"] == "4g"


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

    assert "      o: size=16k,nr_inodes=8,mode=0750,uid=0" in written


@pytest.mark.parametrize(
    "hardening_line",
    [
        "      PRESENTATOR_BUILD_RUNNER: worker",
        "        source: build-socket\n        target: /run/presentator-build",
        "        volume:\n          nocopy: true",
        "      build-worker:\n        condition: service_started",
        "    cap_drop:\n      - ALL",
        "    security_opt:\n      - no-new-privileges:true",
        "    read_only: true",
        "      - /tmp:size=512m,nr_inodes=10k,mode=1777,nosuid,nodev",
    ],
)
def test_the_instance_builds_through_the_worker_hardened(hardening_line: str) -> None:
    """B16: the web's flipped and hardened service in compose."""
    assert hardening_line in "\n".join(_service_lines("presentator"))


@pytest.mark.parametrize(
    "authority",
    [
        "docker.sock",
        "group_add",
        "DOCKER_GROUP",
        "PRESENTATOR_BUILD_IMAGE",
        "PRESENTATOR_BUILD_VOLUME",
        "build-sandbox",
        "x-build-image",
        "profiles:",
    ],
)
def test_the_instance_holds_no_authority_over_this_machines_docker(
    authority: str,
) -> None:
    """B16 and B17: no socket, docker group, sandbox service or profile in the file."""
    assert authority not in _COMPOSE.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "host_path", "setting"),
    [
        (
            "presentator_secret_key",
            "${HOME}/.secrets/presentator/secret-key",
            "PRESENTATOR_SECRET_KEY_FILE",
        ),
        (
            "oidc_client_secret",
            "${HOME}/.secrets/keycloak/clients/presentator",
            "PRESENTATOR_OIDC_CLIENT_SECRET_FILE",
        ),
    ],
    ids=["master key", "client secret"],
)
def test_each_secret_is_a_file_secret_from_home_that_only_the_instance_mounts(
    name: str, host_path: str, setting: str
) -> None:
    """edge#280: every app secret reaches the container as a mounted file."""
    compose = _parsed_compose()

    assert compose["secrets"][name]["file"] == host_path
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
