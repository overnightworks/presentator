"""The cutover command's second run probes the deployed instance, read-only."""

import enum
import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

from presentator.api.auth import (
    _DATENSCHUTZ,  # pyright: ignore[reportPrivateUsage] - the app keeps its one copy of the path private
    START_SIGN_IN_PATH,
)
from presentator.api.health import HEALTH_PATH
from tests.scripts.support.cutover_host import (
    COOKIE_VALUE,
    DARK_CURL_EXITS,
    HARDENED,
    LOBBY_HOST,
    NETWORK_NAME,
    PROXY_ADDRESS,
    REFUSED,
    SIGN_IN_ANSWER,
    TALK_HOST,
    WEB_CONTAINER_ID,
    Host,
)

DEPLOYED_FOLDER = ".local/state/edge/deployed"
NEXT_STEP = "next: hal stop presentator"
NOT_DEPLOYED = "not deployed yet: run hal apply"
RECORD_UNREADABLE = "refused: edge's deployed record of presentator is not readable"
RECORDS_MISSING = (
    "refused: edge's deployed records are not in ~/.local/state/edge/deployed"
)
NOT_HEAD = "refused: the deployed version is not the checkout's HEAD"
COMPOSE_KEYS = (
    "PRESENTATOR_PUBLIC_ORIGIN",
    "PRESENTATOR_TALK_ORIGIN",
    "PRESENTATOR_TRUSTED_PROXIES",
    "traefik.docker.network",
)
INSPECTED_FIELDS = {
    ".Mounts",
    ".HostConfig.GroupAdd",
    ".HostConfig.PortBindings",
    ".IPAM.Config",
}
FIRST_RUN_HEAD = "d" * 40
WEB_PROBES_IN_ORDER = (
    ("lobby", "root"),
    ("talk", "root"),
    ("lobby", "sign-in"),
    ("talk", "sign-in"),
    ("lobby", "datenschutz"),
    ("lobby", "health"),
    ("talk", "health"),
)
CONTAINER_LINES = [
    "held: web container",
    "held: web container mounts",
    "held: web container groups",
    "held: web container ports",
    "held: web container secrets",
    "held: web container gateway",
]


class Absent(enum.Enum):
    """What the test removes from the deployed folder."""

    FILE = enum.auto()
    FOLDER = enum.auto()


Record = Mapping[str, object] | list[str] | str | Absent


@dataclass
class Run:
    """The exit code and the printed lines of one run."""

    code: int
    lines: list[str]


@dataclass
class Scene:
    """One home with a deployed record and a checkout whose compose file is known."""

    home: Path
    checkout: Path
    host: Host
    cutover: ModuleType

    @property
    def record(self) -> Path:
        return self.home / DEPLOYED_FOLDER / "presentator"

    def deploy(self, content: Record) -> None:
        text = content if isinstance(content, str) else json.dumps(content)
        self.record.write_text(text)

    def arrange(self, record: Record) -> None:
        if record is Absent.FILE:
            self.record.unlink()
        elif record is Absent.FOLDER:
            shutil.rmtree(self.record.parent)
        else:
            self.deploy(record)

    def compose(self, text: str) -> None:
        (self.checkout / "compose.yaml").write_text(text)

    def drive(self, uid: int = 1000) -> Run:
        lines: list[str] = []
        code = self.cutover.cut_over(
            home=self.home,
            checkout=self.checkout,
            run=self.host,
            say=lines.append,
            effective_uid=uid,
            passwd_home=self.home,
        )
        return Run(code, lines)

    def probing_calls(self) -> list[tuple[str, ...]]:
        probes = [call for call in self.host.commands("curl") if "--include" in call]
        inspects = [call for call in self.host.commands("docker") if "inspect" in call]
        return [*probes, *inspects]


COMPOSE = f"""\
services:
  presentator:
    labels:
      traefik.docker.network: {NETWORK_NAME}
    environment:
      PRESENTATOR_PUBLIC_ORIGIN: https://{LOBBY_HOST}
      PRESENTATOR_OIDC_ISSUER: https://issuer.example.test/realms/x
      PRESENTATOR_TALK_ORIGIN: https://{TALK_HOST}
      PRESENTATOR_TRUSTED_PROXIES: {PROXY_ADDRESS}
"""


@pytest.fixture
def scene(cutover: ModuleType, home: Path, host: Host, tmp_path: Path) -> Scene:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    made = Scene(home, checkout, host, cutover)
    made.deploy({"tag": "v1.0.0", "sha": FIRST_RUN_HEAD, "time": "then"})
    made.compose(COMPOSE)
    made.host.containers |= {"bw0001": "presentator", "dead01": "presentator"}
    made.host.services |= {"bw0001": "build-worker"}
    made.host.stopped |= {"dead01"}
    return made


def test_a_refused_or_failed_retire_probes_nothing(scene: Scene) -> None:
    """S9h2 (#553) sentence 1: only a finished retire runs the probes."""
    refused = scene.drive(uid=0)
    scene.host.failing = {"docker network ls"}
    failed = scene.drive()

    assert (refused.code, failed.code) == (REFUSED, 1)
    assert not scene.probing_calls()


def test_a_finished_retire_is_followed_by_the_probes(scene: Scene) -> None:
    """S9h2 (#553) sentence 1: retire's lines first, then the probe lines."""
    run = scene.drive()

    assert run.code == 0
    assert "removed: presentator-live" in run.lines
    assert run.lines.index("removed: presentator-live") < run.lines.index(
        "held: local lobby root"
    )


def test_the_second_run_reports_the_retire_as_gone_and_probes_the_same(
    scene: Scene,
) -> None:
    """S9h2 (#553) sentence 1: the same command twice decides from state."""
    first = scene.drive()
    second = scene.drive()

    assert "already gone: presentator-live" in second.lines
    probes = [line for line in first.lines if line.startswith("held:")]
    assert probes
    assert [line for line in second.lines if line.startswith("held:")] == probes
    assert (first.code, second.code) == (0, 0)


@pytest.mark.parametrize(
    ("record", "code", "last_line"),
    [
        pytest.param(Absent.FILE, 0, NOT_DEPLOYED, id="no-file"),
        pytest.param(
            Absent.FOLDER,
            REFUSED,
            RECORDS_MISSING,
            id="no-folder",
        ),
        pytest.param(
            "{not json",
            REFUSED,
            RECORD_UNREADABLE,
            id="not-json",
        ),
        pytest.param(
            ["v1.0.0"],
            REFUSED,
            RECORD_UNREADABLE,
            id="not-an-object",
        ),
        pytest.param(
            {"sha": FIRST_RUN_HEAD},
            REFUSED,
            RECORD_UNREADABLE,
            id="no-tag",
        ),
        pytest.param(
            {"tag": "v1.0.0", "sha": 7},
            REFUSED,
            RECORD_UNREADABLE,
            id="sha-not-a-string",
        ),
        pytest.param(
            {"tag": "v1.0.0", "sha": FIRST_RUN_HEAD, "extra": [1], "x": None},
            0,
            "held: public talk health",
            id="unknown-keys-ignored",
        ),
    ],
)
def test_the_deployed_record_decides_whether_anything_is_probed(
    scene: Scene,
    record: Record,
    code: int,
    last_line: str,
) -> None:
    """S9h2 (#553) sentence 2: missing, unreadable and extra-key records."""
    scene.arrange(record)

    run = scene.drive()

    assert (run.code, run.lines[-1]) == (code, last_line)
    assert bool(scene.probing_calls()) is (last_line.startswith("held"))


def test_the_record_and_its_folder_are_left_exactly_as_found(scene: Scene) -> None:
    """S9h2 (#553) sentence 2: never written, deleted or locked."""
    folder = scene.record.parent
    before = (scene.record.read_bytes(), scene.record.stat().st_mtime_ns)
    listing = sorted(path.name for path in folder.iterdir())

    scene.drive()

    assert (scene.record.read_bytes(), scene.record.stat().st_mtime_ns) == before
    assert sorted(path.name for path in folder.iterdir()) == listing


@pytest.mark.parametrize(
    "stale",
    [
        pytest.param({"tag": "v0.9.0", "sha": FIRST_RUN_HEAD}, id="other-tag"),
        pytest.param({"tag": "v1.0.0", "sha": "e" * 40}, id="other-commit"),
    ],
)
def test_a_deployed_tag_or_commit_other_than_head_is_refused_without_naming_it(
    scene: Scene, stale: dict[str, str]
) -> None:
    """S9h2 (#553) sentence 3: the record must name HEAD's tag and commit."""
    scene.deploy(stale)

    run = scene.drive()

    assert (run.code, run.lines[-1]) == (REFUSED, NOT_HEAD)
    assert scene.probing_calls() == []
    printed = "\n".join(run.lines)
    assert not {"v0.9.0", "v1.0.0", FIRST_RUN_HEAD, "e" * 40} & set(printed.split())


@pytest.mark.parametrize("key", COMPOSE_KEYS)
@pytest.mark.parametrize("shape", ["missing", "twice"])
def test_a_compose_key_that_is_not_one_line_is_refused_by_name(
    scene: Scene, key: str, shape: str
) -> None:
    """S9h2 (#553) sentence 4: each compared value is exactly one line."""
    lines = COMPOSE.splitlines()
    line = next(entry for entry in lines if key in entry)
    scene.compose(
        "\n".join(entry for entry in lines if entry != line)
        + ("\n" + line + "\n" + line if shape == "twice" else "")
        + "\n"
    )

    run = scene.drive()

    assert run.code == REFUSED
    assert run.lines[-1].startswith("refused:")
    assert key in run.lines[-1]
    assert scene.probing_calls() == []


def test_the_scan_of_the_real_compose_file_agrees_with_a_yaml_parse(
    cutover: ModuleType,
) -> None:
    """S9h2 (#553) sentence 4: the line scan reads what the parser reads."""
    path = Path(__file__).parents[2] / "compose.yaml"
    document: dict[str, Any] = YAML(typ="safe").load(  # pyright: ignore[reportUnknownMemberType]
        path.read_text()
    )
    service = document["services"]["presentator"]

    found = cutover.deployment_of(path.read_text())

    environment = service["environment"]
    assert found.lobby_origin == environment["PRESENTATOR_PUBLIC_ORIGIN"]
    assert found.talk_origin == environment["PRESENTATOR_TALK_ORIGIN"]
    assert found.trusted_proxies == environment["PRESENTATOR_TRUSTED_PROXIES"]
    assert found.network == service["labels"]["traefik.docker.network"]


def test_the_scripts_app_paths_are_the_apps_own(cutover: ModuleType) -> None:
    """S9h2 (#553) sentence 4: python3 cannot import the app, a test ties the copies."""
    assert cutover.SIGN_IN_PATH == START_SIGN_IN_PATH
    assert cutover.HEALTH_PATH == HEALTH_PATH
    assert cutover.DATENSCHUTZ_PATH == _DATENSCHUTZ


def test_every_probe_holds_on_a_healthy_instance(scene: Scene) -> None:
    """S9h2 (#553) sentence 5: one held line per probe, in order, exit 0."""
    run = scene.drive()
    probe_lines = run.lines[run.lines.index("held: local lobby root") :]

    assert run.code == 0
    assert probe_lines == [
        *(f"held: local {side} {name}" for side, name in WEB_PROBES_IN_ORDER),
        *CONTAINER_LINES,
        *(f"held: public {side} {name}" for side, name in WEB_PROBES_IN_ORDER),
    ]


NOT_HARDENED = "HTTP/1.1 302 Found\r\nlocation: /login/oidc\r\n\r\n"
PLAIN_COOKIE = SIGN_IN_ANSWER.replace("__Host-", "")
OPEN_COOKIE = SIGN_IN_ANSWER.replace("; Secure", "")
BAD_LOBBY = "HTTP/1.1 500 Oops\r\n\r\n"
SECURITY_HEADERS = HARDENED.removesuffix("\r\n").split("\r\n")
HSTS_ONLY = SECURITY_HEADERS[0] + "\r\n\r\n"
CSP_ONLY = SECURITY_HEADERS[1] + "\r\n\r\n"


def hardened_answer(status_line: str, location: str) -> str:
    return f"HTTP/1.1 {status_line}\r\nlocation: {location}\r\n{HARDENED}"


def local(host: str, path: str, answer: str) -> dict[tuple[str, str, str], str]:
    return {("local", host, path): answer}


@pytest.mark.parametrize(
    ("overrides", "failed"),
    [
        pytest.param(
            local(LOBBY_HOST, "/", NOT_HARDENED), "local lobby root", id="csp"
        ),
        pytest.param(
            local(TALK_HOST, "/", "HTTP/1.1 200 OK\r\n\r\n"),
            "local talk root",
            id="talk-no-redirect",
        ),
        pytest.param(
            local(LOBBY_HOST, "/", hardened_answer("302 Found", "/")),
            "local lobby root",
            id="lobby-redirects-to-itself",
        ),
        pytest.param(
            local(LOBBY_HOST, "/", hardened_answer("302 Found", "/other")),
            "local lobby root",
            id="lobby-redirects-elsewhere",
        ),
        pytest.param(
            local(
                TALK_HOST,
                "/",
                hardened_answer("303 See Other", "https://elsewhere.example.test"),
            ),
            "local talk root",
            id="talk-redirects-to-another-origin",
        ),
        pytest.param(
            local(
                TALK_HOST,
                "/",
                hardened_answer("303 See Other", f"https://{LOBBY_HOST}.evil"),
            ),
            "local talk root",
            id="talk-redirects-to-a-lookalike-origin",
        ),
        pytest.param(
            local(
                TALK_HOST,
                "/",
                hardened_answer("302 Found", f"https://{LOBBY_HOST}"),
            ),
            "local talk root",
            id="talk-redirects-with-another-status",
        ),
        pytest.param(
            local(
                LOBBY_HOST,
                "/",
                "HTTP/1.1 302 Found\r\nlocation: /login/oidc\r\n" + HSTS_ONLY,
            ),
            "local lobby root",
            id="no-csp-alone",
        ),
        pytest.param(
            local(
                LOBBY_HOST,
                "/",
                "HTTP/1.1 302 Found\r\nlocation: /login/oidc\r\n" + CSP_ONLY,
            ),
            "local lobby root",
            id="no-hsts-alone",
        ),
        pytest.param(
            local(LOBBY_HOST, "/login/oidc", PLAIN_COOKIE),
            "local lobby sign-in",
            id="cookie-without-host-prefix",
        ),
        pytest.param(
            local(TALK_HOST, "/login/oidc", OPEN_COOKIE),
            "local talk sign-in",
            id="cookie-without-secure",
        ),
        pytest.param(
            local(TALK_HOST, "/login/oidc", "HTTP/1.1 302 Found\r\n\r\n"),
            "local talk sign-in",
            id="no-cookie",
        ),
        pytest.param(
            local(LOBBY_HOST, "/datenschutz", BAD_LOBBY),
            "local lobby datenschutz",
            id="datenschutz",
        ),
        pytest.param(
            {("public", TALK_HOST, "/health"): BAD_LOBBY},
            "public talk health",
            id="public-health",
        ),
    ],
)
def test_a_failed_probe_is_named_and_the_others_still_run(
    scene: Scene, overrides: dict[tuple[str, str, str], str], failed: str
) -> None:
    """S9h2 (#553) sentences 5 and 6: failed line, rest runs, hal stop, exit 1."""
    scene.host.overrides = overrides

    run = scene.drive()

    assert f"failed: {failed}" in run.lines
    assert "held: public talk health" in run.lines or failed == "public talk health"
    assert run.lines[-1] == NEXT_STEP
    assert run.code == 1
    assert run.lines.count(NEXT_STEP) == 1


ONE_SECRET: dict[str, object] = {
    "Type": "bind",
    "Destination": "/run/secrets/one",
    "RW": False,
}
BOUND_PORT: dict[str, str] = {}
CONTAINER_FAULTS = [
    pytest.param(
        {".Mounts": [{"Source": "/x", "Destination": "/var/run/docker.sock"}]},
        "web container mounts",
        id="docker-socket",
    ),
    pytest.param({".HostConfig.GroupAdd": ["0"]}, "web container groups", id="groups"),
    pytest.param(
        {".HostConfig.PortBindings": {"8000/tcp": [BOUND_PORT]}},
        "web container ports",
        id="ports",
    ),
    pytest.param(
        {".Mounts": [ONE_SECRET]},
        "web container secrets",
        id="one-secret",
    ),
    pytest.param(
        {".IPAM.Config": [{"Gateway": "192.0.2.1"}]},
        "web container gateway",
        id="other-gateway",
    ),
    pytest.param(
        {".IPAM.Config": "garbled"}, "web container gateway", id="garbled-answer"
    ),
]


@pytest.mark.parametrize(("inspected", "failed"), CONTAINER_FAULTS)
def test_a_web_container_that_breaks_the_hardened_shape_fails_its_probe(
    scene: Scene, inspected: dict[str, object], failed: str
) -> None:
    """S9h2 (#553) sentences 5 and 6: the inspected shape, field by field."""
    scene.host.inspected |= inspected

    run = scene.drive()

    assert f"failed: {failed}" in run.lines
    assert (run.code, run.lines[-1]) == (1, NEXT_STEP)


@pytest.mark.parametrize("empty", [None, []], ids=["null", "empty-list"])
def test_a_web_container_without_added_groups_holds_in_either_empty_form(
    scene: Scene, empty: list[str] | None
) -> None:
    """S9h2 (#553) sentence 5: .HostConfig.GroupAdd is empty."""
    scene.host.inspected |= {".HostConfig.GroupAdd": empty}

    run = scene.drive()

    assert "held: web container groups" in run.lines
    assert run.code == 0


def test_a_secret_mount_that_is_writable_or_outside_the_secrets_folder_fails(
    scene: Scene,
) -> None:
    """S9h2 (#553) sentence 5: both mounts are read-only binds below ~/.secrets."""
    secrets = scene.host.inspected[".Mounts"]
    assert isinstance(secrets, list)
    scene.host.inspected |= {".Mounts": [{**secrets[0], "RW": True}, secrets[1]]}
    writable = scene.drive()
    scene.host.inspected |= {
        ".Mounts": [{**secrets[0], "Source": "/etc/shadow"}, secrets[1]]
    }
    elsewhere = scene.drive()

    assert "failed: web container secrets" in writable.lines
    assert "failed: web container secrets" in elsewhere.lines


@pytest.mark.parametrize("containers", [{}, {"a1": "presentator", "a2": "presentator"}])
def test_other_than_one_web_container_fails_the_container_probe(
    scene: Scene, containers: dict[str, str]
) -> None:
    """S9h2 (#553) sentence 5: exactly one running container, else it fails."""
    scene.host.containers = containers

    run = scene.drive()

    assert "failed: web container" in run.lines
    assert (run.code, run.lines[-1]) == (1, NEXT_STEP)


@pytest.mark.parametrize("exit_code", DARK_CURL_EXITS)
def test_a_dark_public_host_is_not_exercised_and_does_not_fail_the_run(
    scene: Scene, exit_code: int
) -> None:
    """S9h2 (#553) sentences 5 and 6: curl exit 6 or 7 is a dark host, exit 0 stays."""
    scene.host.dark = {("public", host): exit_code for host in (LOBBY_HOST, TALK_HOST)}

    run = scene.drive()

    assert "not exercised: public lobby root" in run.lines
    assert "not exercised: public talk health" in run.lines
    assert "held: local lobby root" in run.lines
    assert not [line for line in run.lines if line.startswith("failed")]
    assert (run.code, NEXT_STEP in run.lines) == (0, False)


@pytest.mark.parametrize("exit_code", DARK_CURL_EXITS)
def test_a_local_host_that_does_not_answer_is_a_failure_not_a_dark_host(
    scene: Scene, exit_code: int
) -> None:
    """S9h2 (#553) sentence 5: only the public probes can be dark."""
    scene.host.dark = {("local", LOBBY_HOST): exit_code}

    run = scene.drive()

    assert "failed: local lobby root" in run.lines
    assert not [line for line in run.lines if line.startswith("not exercised: local")]
    assert (run.code, run.lines[-1]) == (1, NEXT_STEP)


def test_the_web_container_is_the_running_presentator_service_and_its_compose_network(
    scene: Scene,
) -> None:
    """S9h2 (#553) sentence 5: build-worker and stopped containers are not it."""
    run = scene.drive()

    assert "held: web container" in run.lines
    assert run.code == 0
    inspects = [call for call in scene.host.commands("docker") if "inspect" in call]
    assert {call[-1] for call in inspects} == {WEB_CONTAINER_ID, NETWORK_NAME}


def test_docker_is_asked_for_named_fields_only(scene: Scene) -> None:
    """S9h2 (#553) sentence 7: one field per call, never Config, Env or compose."""
    scene.drive()

    inspects = [
        call
        for call in scene.host.commands("docker")
        if call[1] == "inspect" or call[1:3] == ("network", "inspect")
    ]
    fields = {call[call.index("--format") + 1] for call in inspects}
    assert fields == {f"{{{{json {name}}}}}" for name in INSPECTED_FIELDS}
    flat = " ".join(" ".join(call) for call in scene.host.commands("docker"))
    assert not {"exec", "compose", "Env", ".Config}}"} & set(flat.split())


def test_no_request_follows_a_redirect_or_carries_a_token(scene: Scene) -> None:
    """S9h2 (#553) sentence 8: no -L, no Authorization header, no issuer call."""
    scene.drive()

    asked = [call for call in scene.host.commands("curl") if "--include" in call]
    assert asked
    words = {word for call in asked for word in call}
    assert not {"-L", "--location", "--location-trusted"} & words
    assert not any("authorization" in word.lower() for word in words)
    assert not any("issuer" in word for word in words)
    urls = {call[-1] for call in asked}
    assert {url.split("/")[2] for url in urls} == {
        "127.0.0.1:8081",
        LOBBY_HOST,
        TALK_HOST,
    }


def test_the_output_carries_no_value_a_program_or_file_returned(
    scene: Scene,
) -> None:
    """S9h2 (#553) sentence 7: fixed words only, no header, mount, id or gateway."""
    scene.host.overrides = {("local", LOBBY_HOST, "/health"): BAD_LOBBY}

    run = scene.drive()
    printed = "\n".join(run.lines)

    for value in (
        COOKIE_VALUE,
        "__Host-",
        "session",
        PROXY_ADDRESS,
        NETWORK_NAME,
        "5afe01",
        str(scene.home / ".secrets"),
        "v1.0.0",
        FIRST_RUN_HEAD,
        LOBBY_HOST,
        "max-age",
    ):
        assert value not in printed
    assert HTTPStatus.OK.phrase not in printed
