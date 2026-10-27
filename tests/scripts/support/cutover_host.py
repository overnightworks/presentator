"""Docker, systemd, git, curl and Traefik as one fake behind the cutover runner."""

import importlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from urllib.parse import urlsplit

LIVE_HEAD = "1f0422c" + "a" * 33
ORIGIN = "https://github.com/overnightworks/presentator.git"
UNIT_DIRECTORY = ".config/systemd/user"
SURVIVORS = (
    "presentator-stage/keep",
    ".cache/huggingface/keep",
    "srv/presentator/keep",
    ".local/state/edge/deployed/hopin",
    ".claude/projects/-home-felix-hummert-git-presentator/keep",
    ".claude/projects/-git-agent-presentator/keep",
)
LISTED_PATHS = (
    "presentator-live",
    "presentator-backups",
    ".local/state/agent-presentator",
    ".local/state/presentator-speech",
    f"{UNIT_DIRECTORY}/presentator-speech.service",
    f"{UNIT_DIRECTORY}/presentator-copresenter.service",
    f"{UNIT_DIRECTORY}/presentator-copresenter.service.d/no-sync.conf",
)
VOLUMES = (
    "presentator-live_database",
    "presentator-live_builds",
    "presentator-live_mirrors",
)
KEPT_VOLUMES = ("presentator_database", "presentator_builds")
IMAGE = "presentator-adoption-instance:ap112-e04e837a"
REFUSED = 2
SERVICE_LABEL = "label=com.docker.compose.service="
UNITS = ("presentator-speech.service", "presentator-copresenter.service")
UNIT_EXIT_CODES = {"stop": 5, "disable": 1, "reset-failed": 1}
LOBBY_HOST = "lobby.example.test"
TALK_HOST = "talk.example.test"
PROXY_ADDRESS = "10.9.8.7"
NETWORK_NAME = "web-net_default"
COOKIE_VALUE = "cookie-value-4471"
WEB_CONTAINER_ID = "5afe01"
DARK_CURL_EXITS = (6, 7)
SIGN_IN_ANSWER = (
    "HTTP/1.1 302 Found\r\n"
    f"set-cookie: __Host-session={COOKIE_VALUE}; Path=/; Secure; HttpOnly\r\n\r\n"
)
OK_ANSWER = "HTTP/1.1 200 OK\r\ncontent-type: text/html\r\n\r\n"
HARDENED = (
    "strict-transport-security: max-age=63072000\r\n"
    "content-security-policy: default-src 'self'\r\n\r\n"
)


@dataclass
class Host:
    """Docker, systemd, git and Traefik as one mutable fake behind the runner."""

    home: Path
    calls: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])
    containers: dict[str, str] = field(
        default_factory=lambda: {
            "c0ffee": "presentator-live",
            WEB_CONTAINER_ID: "presentator",
            "b10b02": "hopin",
        }
    )
    services: dict[str, str] = field(default_factory=dict[str, str])
    stopped: set[str] = field(default_factory=set[str])
    networks: list[str] = field(
        default_factory=lambda: ["presentator-live_default", "presentator_default"]
    )
    volumes: list[str] = field(default_factory=lambda: [*VOLUMES, *KEPT_VOLUMES])
    images: list[str] = field(
        default_factory=lambda: [
            IMAGE,
            "presentator-adoption-other:x",
            "presentator:v1",
        ]
    )
    traefik_answers: bool = True
    failing: set[str] = field(default_factory=set[str])
    exits_after_acting: set[str] = field(default_factory=set[str])
    failed_units: set[str] = field(default_factory=set[str])
    active_units: set[str] = field(
        default_factory=lambda: {"presentator-speech.service"}
    )
    stopped_or_disabled: list[tuple[str, str]] = field(
        default_factory=list[tuple[str, str]]
    )
    lingering: set[str] = field(default_factory=set[str])
    enabled_units: set[str] = field(default_factory=lambda: set(UNITS))
    stuck_enabled: set[str] = field(default_factory=set[str])
    checkout_on_tag: bool = True
    checkout_status: str = ""
    live_remotes: str = "origin"
    live_origin: str = ORIGIN
    live_head: str = LIVE_HEAD
    live_status: str = "?? .env.bak-preupgrade\n?? compose.override.yaml"
    overrides: dict[tuple[str, str, str], str] = field(
        default_factory=dict[tuple[str, str, str], str]
    )
    dark: dict[tuple[str, str], int] = field(default_factory=dict[tuple[str, str], int])
    inspected: dict[str, object] = field(default_factory=dict[str, object])
    live_refs: str = (
        f"{LIVE_HEAD} refs/heads/main\n"
        f"{'b' * 40} refs/remotes/origin/HEAD\n"
        f"{'c' * 40} refs/remotes/origin/claude/issue-1-x"
    )

    def __post_init__(self) -> None:
        """Fill in the inspect answers of a healthy web container and its network."""
        secret = {"Type": "bind", "RW": False}
        self.inspected = {
            ".Mounts": [
                {
                    **secret,
                    "Source": str(self.home / ".secrets" / name),
                    "Destination": f"/run/secrets/{name}",
                }
                for name in ("first", "second")
            ],
            ".HostConfig.GroupAdd": None,
            ".HostConfig.PortBindings": {},
            ".IPAM.Config": [{"Subnet": "10.9.8.0/24", "Gateway": PROXY_ADDRESS}],
            **self.inspected,
        }

    def __call__(self, arguments: Sequence[str]) -> object:
        command = tuple(arguments)
        self.calls.append(command)
        program, rest = command[0], command[1:]
        if any(
            word in self.failing
            for word in (" ".join(command[:3]), " ".join(command[:2]))
        ):
            return outcome(1, "")
        handlers = {
            "curl": self.run_curl,
            "git": self.run_git,
            "docker": self.run_docker,
            "systemctl": self.run_systemctl,
        }
        return handlers[program](rest)

    def run_curl(self, rest: tuple[str, ...]) -> object:
        if "--include" in rest:
            return self.run_probe_curl(rest)
        return outcome(0 if self.traefik_answers else 7, "")

    def run_probe_curl(self, rest: tuple[str, ...]) -> object:
        url = urlsplit(rest[-1])
        local = "--header" in rest
        host = rest[rest.index("--header") + 1].removeprefix("Host: ") if local else ""
        host = host or url.hostname or ""
        vantage = "local" if local else "public"
        if (vantage, host) in self.dark:
            return outcome(self.dark[vantage, host], "")
        override = self.overrides.get((vantage, host, url.path))
        return outcome(0, override or self.healthy_answer(host, url.path))

    def healthy_answer(self, host: str, path: str) -> str:
        if path in {"/health", "/datenschutz"}:
            return OK_ANSWER
        if path == "/login/oidc":
            return SIGN_IN_ANSWER
        if host == TALK_HOST:
            return f"HTTP/1.1 303 See Other\r\nlocation: https://{LOBBY_HOST}\r\n{HARDENED}"
        return f"HTTP/1.1 302 Found\r\nlocation: /login/oidc?next=%2F\r\n{HARDENED}"

    def run_git(self, rest: tuple[str, ...]) -> object:
        path, words = Path(rest[2]), rest[3:]
        live = path == self.home / "presentator-live"
        answers: dict[tuple[str, ...], str] = {
            ("rev-parse", "HEAD"): self.live_head if live else "d" * 40,
            ("tag", "--list", "v*", "--points-at", "HEAD"): (
                "" if live or not self.checkout_on_tag else "v1.0.0"
            ),
            ("status", "--porcelain"): self.live_status
            if live
            else self.checkout_status,
            ("remote",): self.live_remotes,
            ("remote", "get-url", "origin"): self.live_origin,
            ("for-each-ref", "--format=%(objectname) %(refname)"): self.live_refs,
        }
        return outcome(0, answers[words])

    def run_docker(self, rest: tuple[str, ...]) -> object:
        match rest:
            case ("ps", *_) | ("inspect", *_) | ("network", "inspect", *_):
                return self.answer_query(rest)
            case ("stop", _):
                return outcome(0, "")
            case ("rm", container):
                self.remove_container(container)
            case ("network", "ls", *_):
                return outcome(0, "\n".join(self.networks))
            case ("network", "rm", name):
                self.remove(self.networks, name)
            case ("volume", "ls", *_):
                return outcome(0, "\n".join(self.volumes))
            case ("volume", "rm", name):
                self.volumes.remove(name)
            case ("image", "ls", *_):
                return outcome(0, "\n".join(self.images))
            case ("image", "rm", "--no-prune", reference):
                self.images.remove(reference)
            case _:
                pass
        return outcome(0, "")

    def run_inspect(self, rest: tuple[str, ...]) -> object:
        """Answer only for the web container and for the network compose names."""
        template, target = rest[rest.index("--format") + 1], rest[-1]
        known = NETWORK_NAME if rest[0] == "network" else WEB_CONTAINER_ID
        if target != known:
            return outcome(1, "")
        field_name = template.removeprefix("{{json ").removesuffix("}}")
        return outcome(0, json.dumps(self.inspected[field_name]))

    def answer_query(self, rest: tuple[str, ...]) -> object:
        if rest[0] != "ps":
            return self.run_inspect(rest)
        if any(SERVICE_LABEL in word for word in rest):
            return self.run_service_ps(rest)
        return self.run_ps(rest)

    def run_ps(self, rest: tuple[str, ...]) -> object:
        label = rest[rest.index("--filter") + 1]
        project = label.removeprefix("label=com.docker.compose.project=")
        matching = [name for name, owner in self.containers.items() if owner == project]
        return outcome(0, "\n".join(matching))

    def run_service_ps(self, rest: tuple[str, ...]) -> object:
        """List the running containers with both the project and the service label."""
        labels = dict(
            re.findall(
                r"label=com\.docker\.compose\.(\w+)=(.*)",
                "\n".join(
                    value for flag, value in pairwise(rest) if flag == "--filter"
                ),
            )
        )
        matching = [
            name
            for name, project in self.containers.items()
            if labels.get("project") == project
            and labels.get("service") == self.services.get(name, project)
            and name not in self.stopped
        ]
        return outcome(0, "\n".join(matching))

    def remove_container(self, container: str) -> None:
        if container not in self.lingering:
            del self.containers[container]

    def remove(self, names: list[str], name: str) -> None:
        if name not in self.lingering:
            names.remove(name)

    def run_systemctl(self, rest: tuple[str, ...]) -> object:
        assert rest[0] == "--user"
        verb, unit = rest[1], rest[-1]
        known = (self.home / UNIT_DIRECTORY / unit).exists()
        if verb == "show" and "UnitFileState" in rest:
            enabled = unit in self.enabled_units
            return outcome(0, "enabled\n" if enabled else "disabled\n")
        if verb == "show":
            load = "loaded" if known else "not-found"
            active = "inactive"
            if unit in self.active_units:
                active = "active"
            if unit in self.failed_units:
                active = "failed"
            return outcome(0, f"LoadState={load}\nActiveState={active}\n")
        self.act(verb, unit)
        if f"systemctl --user {verb}" in self.exits_after_acting:
            return outcome(1, "")
        if verb == "reset-failed" and unit in self.failed_units:
            self.failed_units.discard(unit)
            return outcome(0, "")
        if verb in UNIT_EXIT_CODES and not known:
            return outcome(UNIT_EXIT_CODES[verb], "")
        return outcome(0, "")

    def act(self, verb: str, unit: str) -> None:
        if verb == "stop":
            self.active_units.discard(unit)
        if verb == "disable" and unit not in self.stuck_enabled:
            self.enabled_units.discard(unit)
            self.wants_link(unit).unlink(missing_ok=True)
        if verb in {"stop", "disable"}:
            self.stopped_or_disabled.append((verb, unit))

    def wants_link(self, unit: str) -> Path:
        return self.home / UNIT_DIRECTORY / "default.target.wants" / unit

    def commands(self, program: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == program]


def outcome(code: int, text: str) -> object:
    return importlib.import_module("cutover").Outcome(code, text)
