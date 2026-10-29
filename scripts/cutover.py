"""One-time command that retires the old `presentator-live` instance.

Run as `python3 scripts/cutover.py`, from the checkout on a `v*` tag, twice
(#508 S9l): after `hal setup --apply` and before edge enables presentator's
catalog entry, then again after `hal apply` has published. Each run first
retires; only a finished retire is followed by the probe run (below). The
retire removes exactly the listed targets in a fixed order (units,
containers, volumes and image, folders), refuses before any change when the
host or the checkout is not what the list assumes, stops at the first failed
step and resumes on the next run; a second run reports every target as
"already gone". It prints one line per target (surviving
`presentator-adoption-*` images are only counted, as ruled) and no value that
Docker, systemd or git produced, and reads no `.env`. The operator's rulings on
#508 lines 1, 2, 3 and 6 are the contract; #508 S9m deletes this file.

The probe run reads edge's deployed record of presentator (never writing it)
and, when it names the checkout's HEAD, probes the instance through Traefik,
the web container's inspected shape and the two public origins. It prints one
`held:`, `failed:` or `not exercised:` line per probe, names no value any
program returned, never touches a token, and on a failed probe ends with
`next: hal stop presentator` and exit 1.
"""

import enum
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

LIVE_NAME = "presentator-live"
LIVE_COMMIT = "1f0422c"
LIVE_ORIGIN = "https://github.com/overnightworks/presentator.git"
LIVE_UNTRACKED = frozenset({"?? .env.bak-preupgrade", "?? compose.override.yaml"})
PROJECT_LABEL = f"label=com.docker.compose.project={LIVE_NAME}"
ADOPTION_IMAGE_PREFIX = "presentator-adoption-"
TRAEFIK_URL = "http://127.0.0.1:8081/"
UNIT_DIRECTORY = ".config/systemd/user"
UNITS = ("presentator-speech.service", "presentator-copresenter.service")
REMAINS = "remains"
STOPPED_UNIT_STATES = frozenset({"inactive", "failed"})
DISABLED_UNIT_FILE_STATE = "disabled"
RETIRED_UNIT_STATE = {"LoadState": "not-found", "ActiveState": "inactive"}
PROJECT_CONTAINERS = ("docker", "ps", "--all", "--quiet", "--filter", PROJECT_LABEL)
DEPLOYED_RECORD = ".local/state/edge/deployed/presentator"
WEB_CONTAINER = (
    "docker",
    "ps",
    "--quiet",
    "--filter",
    "label=com.docker.compose.project=presentator",
    "--filter",
    "label=com.docker.compose.service=presentator",
)
SECRETS_MOUNT = "/run/secrets/"
SECRETS_FOLDER = ".secrets"
SIGN_IN_PATH = "/login/oidc"
HEALTH_PATH = "/health"
DARK_CURL_EXITS = frozenset({6, 7})
HOST_COOKIE_PREFIX = "__Host-"
HARDENING_HEADERS = ("strict-transport-security", "content-security-policy")
RECORDS_MISSING = "edge's deployed records are not in ~/.local/state/edge/deployed"
RECORD_UNREADABLE = "edge's deployed record of presentator is not readable"
NOT_HEAD = "the deployed version is not the checkout's HEAD"
UNEXPECTED_ANSWER = "a program answered something else than asked"
STOP_NEXT_STEP = "next: hal stop presentator"


@dataclass(frozen=True)
class Outcome:
    """What a program answered: its exit code and its standard output."""

    code: int
    text: str


Runner = Callable[[Sequence[str]], Outcome]
Say = Callable[[str], None]


class Step(enum.Enum):
    """The fixed order of the retire; the value names the step in a failure line."""

    UNITS = "units"
    DOCKER = "containers, volumes and image"
    FOLDERS = "folders"


class Kind(enum.Enum):
    """What a target is, so each is removed by its own means."""

    FILE = enum.auto()
    DIRECTORY = enum.auto()
    TREE = enum.auto()
    CONTAINERS = enum.auto()
    NETWORK = enum.auto()
    VOLUME = enum.auto()
    IMAGE = enum.auto()


@dataclass(frozen=True)
class Target:
    """One thing the command removes, with the ruled line that lists it."""

    step: Step
    kind: Kind
    name: str
    ruled_line: int


LIVE_TARGET = Target(Step.FOLDERS, Kind.TREE, LIVE_NAME, 2)
TARGETS = (
    Target(Step.UNITS, Kind.FILE, f"{UNIT_DIRECTORY}/{UNITS[0]}", 3),
    Target(Step.UNITS, Kind.FILE, f"{UNIT_DIRECTORY}/{UNITS[1]}", 3),
    Target(Step.UNITS, Kind.FILE, f"{UNIT_DIRECTORY}/{UNITS[1]}.d/no-sync.conf", 3),
    Target(Step.UNITS, Kind.DIRECTORY, f"{UNIT_DIRECTORY}/{UNITS[1]}.d", 3),
    Target(Step.DOCKER, Kind.CONTAINERS, f"{LIVE_NAME} containers", 2),
    Target(Step.DOCKER, Kind.NETWORK, f"{LIVE_NAME}_default", 2),
    Target(Step.DOCKER, Kind.VOLUME, f"{LIVE_NAME}_database", 1),
    Target(Step.DOCKER, Kind.VOLUME, f"{LIVE_NAME}_builds", 1),
    Target(Step.DOCKER, Kind.VOLUME, f"{LIVE_NAME}_mirrors", 1),
    Target(Step.DOCKER, Kind.IMAGE, "presentator-adoption-instance:ap112-e04e837a", 2),
    LIVE_TARGET,
    Target(Step.FOLDERS, Kind.TREE, "presentator-backups", 2),
    Target(Step.FOLDERS, Kind.TREE, ".local/state/agent-presentator", 2),
    Target(Step.FOLDERS, Kind.TREE, ".local/state/presentator-speech", 2),
)
PATH_KINDS = (Kind.FILE, Kind.DIRECTORY, Kind.TREE)


class StepFailedError(Exception):
    """A step could not finish; it names the program and verb, never a value."""

    def __init__(self, *words: str) -> None:
        """Join the words into one failure line."""
        super().__init__(" ".join(words))


def real_runner(arguments: Sequence[str]) -> Outcome:
    """Run a program by argument vector, the process boundary of the command."""
    finished = subprocess.run(  # noqa: S603 - the script chooses every executable and option itself and only fills in trusted operator configuration and local Docker discovery and runs without a shell
        arguments, capture_output=True, text=True, check=False
    )
    return Outcome(finished.returncode, finished.stdout)


def exists(path: Path) -> bool:
    """Whether the path is there, a dangling link included."""
    return os.path.lexists(path)


class Retirement:
    """The refusals, steps and reporting of one run against one home."""

    def __init__(self, home: Path, checkout: Path, run: Runner, say: Say) -> None:
        """Bind the home, the script's checkout, the runner and the output."""
        self.home = home
        self.checkout = checkout
        self.run = run
        self.say = say
        self.live = home / LIVE_TARGET.name
        self.retiring = home / f"{LIVE_NAME}.retiring"

    def _lines(self, arguments: Sequence[str]) -> list[str] | None:
        outcome = self.run(arguments)
        return outcome.text.splitlines() if outcome.code == 0 else None

    def _lines_or_fail(self, arguments: Sequence[str]) -> list[str]:
        outcome = self.run(arguments)
        if outcome.code != 0:
            raise StepFailedError(*arguments[:2], f"exit {outcome.code}")
        return outcome.text.splitlines()

    def _git(self, path: Path, *arguments: str) -> list[str] | None:
        return self._lines(("git", "--no-optional-locks", "-C", str(path), *arguments))

    def refusal(self, effective_uid: int, passwd_home: Path) -> str | None:
        """The first reason this run must change nothing, or None."""
        return (
            self._checkout_refusal()
            or self._identity_refusal(effective_uid, passwd_home)
            or self._host_refusal()
            or self._paths_refusal()
            or self._live_checkout_refusal()
        )

    def _checkout_refusal(self) -> str | None:
        on_tag = self._git(self.checkout, "tag", "--list", "v*", "--points-at", "HEAD")
        if not on_tag:
            return "the checkout's HEAD is not the commit of a v* tag"
        if self._git(self.checkout, "status", "--porcelain") != []:
            return "the checkout has uncommitted changes"
        return None

    def _identity_refusal(self, effective_uid: int, passwd_home: Path) -> str | None:
        if effective_uid == 0:
            return "running as root"
        if self.home != passwd_home:
            return "HOME differs from the home in the password database"
        return None

    def _host_refusal(self) -> str | None:
        project = self._lines(PROJECT_CONTAINERS)
        traefik = self.run(
            ("curl", "--silent", "--output", os.devnull, "--max-time", "5", TRAEFIK_URL)
        )
        if not project and traefik.code != 0:
            return f"not this machine: no project {LIVE_NAME} and no Traefik answer"
        return None

    def _paths_refusal(self) -> str | None:
        listed = [t.name for t in TARGETS if t.kind in PATH_KINDS]
        for name in (*listed, f"{LIVE_NAME}.retiring"):
            path = self.home / name
            if exists(path) and path.resolve() != self.home.resolve() / name:
                return f"{name} resolves outside its listed place"
        if exists(self.live) and exists(self.retiring):
            return f"both {LIVE_NAME} and {LIVE_NAME}.retiring exist"
        return None

    def _live_checkout_refusal(self) -> str | None:
        if not exists(self.live):
            return None
        head = self._git(self.live, "rev-parse", "HEAD")
        if head is None or not (self.live / ".git").exists():
            return f"{LIVE_NAME} is not a git checkout"
        checks = (
            (
                self._git(self.live, "remote") == ["origin"],
                "has a remote other than origin",
            ),
            (
                self._git(self.live, "remote", "get-url", "origin") == [LIVE_ORIGIN],
                "has another origin",
            ),
            (
                len(head) == 1 and head[0].startswith(LIVE_COMMIT),
                f"HEAD is not {LIVE_COMMIT}",
            ),
            (
                self._live_status_is_known(),
                "has changes beyond the two known untracked files",
            ),
            (
                self._live_refs_are_known(),
                "has a ref beyond main and origin's remote-tracking refs",
            ),
        )
        for passed, reason in checks:
            if not passed:
                return f"{LIVE_NAME} {reason}"
        return None

    def _live_status_is_known(self) -> bool:
        status = self._git(self.live, "status", "--porcelain")
        return status is not None and sorted(status) == sorted(LIVE_UNTRACKED)

    def _live_refs_are_known(self) -> bool:
        refs = self._git(self.live, "for-each-ref", "--format=%(objectname) %(refname)")
        if refs is None:
            return False
        for entry in refs:
            commit, _, ref = entry.partition(" ")
            on_main = ref == "refs/heads/main" and commit.startswith(LIVE_COMMIT)
            if not (on_main or ref.startswith("refs/remotes/origin/")):
                return False
        return True

    def execute(self) -> int:
        """Run the steps in order; stop at the first failure."""
        for step in Step:
            try:
                getattr(self, f"_step_{step.name.lower()}")()
            except StepFailedError as failure:
                self.say(f"failed: {step.value}: {failure}")
                return 1
            except OSError as failure:
                self.say(f"failed: {step.value}: {type(failure).__name__}")
                return 1
        return 0

    def _report(self, target: Target, *, was_present: bool) -> None:
        self.say(f"{'removed' if was_present else 'already gone'}: {target.name}")

    def _targets(self, step: Step, kind: Kind) -> list[Target]:
        return [t for t in TARGETS if t.step is step and t.kind is kind]

    def _step_units(self) -> None:
        for unit in UNITS:
            if exists(self.home / UNIT_DIRECTORY / unit):
                stopped = self._systemctl("stop", unit)
                disabled = self._systemctl("disable", unit)
                if self._unit_state(unit).get("ActiveState") not in STOPPED_UNIT_STATES:
                    raise StepFailedError(
                        unit, "stays active", f"stop exit {stopped.code}"
                    )
                if self._unit_file_state(unit) != DISABLED_UNIT_FILE_STATE:
                    raise StepFailedError(
                        unit, "not disabled", f"disable exit {disabled.code}"
                    )
        for target in self._targets(Step.UNITS, Kind.FILE):
            self._remove_path(target)
        for target in self._targets(Step.UNITS, Kind.DIRECTORY):
            self._remove_path(target)
        self._systemctl("daemon-reload")
        for unit in UNITS:
            if self._unit_state(unit).get("ActiveState") == "failed":
                self._systemctl("reset-failed", unit)
            if self._unit_state(unit) != RETIRED_UNIT_STATE:
                raise StepFailedError(unit, REMAINS)

    def _systemctl(self, *arguments: str) -> Outcome:
        # The exit code is not the answer: on this host stop, disable and
        # reset-failed of an unknown unit exit non-zero. The unit's state is.
        return self.run(("systemctl", "--user", *arguments))

    def _unit_file_state(self, unit: str) -> str:
        shown = self.run(
            ("systemctl", "--user", "show", "-p", "UnitFileState", "--value", unit)
        )
        return shown.text.strip()

    def _unit_state(self, unit: str) -> dict[str, str]:
        shown = self.run(
            (
                "systemctl",
                "--user",
                "show",
                "-p",
                "LoadState",
                "-p",
                "ActiveState",
                unit,
            )
        )
        pairs = (line.partition("=") for line in shown.text.splitlines())
        return {key: value for key, _, value in pairs}

    def _remove_path(self, target: Target) -> None:
        path = self.home / target.name
        was_present = exists(path)
        if was_present and target.kind is Kind.FILE:
            path.unlink()
        elif was_present and target.kind is Kind.DIRECTORY:
            path.rmdir()
        elif was_present:
            shutil.rmtree(path)
        self._report(target, was_present=was_present)

    def _step_docker(self) -> None:
        containers = self._targets(Step.DOCKER, Kind.CONTAINERS)[0]
        found = self._project_containers()
        for container in found:
            self._docker("stop", container)
            self._docker("rm", container)
        self._report(containers, was_present=bool(found))
        network = self._targets(Step.DOCKER, Kind.NETWORK)[0]
        network_present = network.name in self._docker_names("network", "{{.Name}}")
        if network_present:
            self._docker("network", "rm", network.name)
        self._report(network, was_present=network_present)
        if self._project_containers() or network.name in self._docker_names(
            "network", "{{.Name}}"
        ):
            raise StepFailedError(LIVE_NAME, REMAINS)
        for volume in self._targets(Step.DOCKER, Kind.VOLUME):
            present = volume.name in self._docker_names("volume", "{{.Name}}")
            if present:
                self._docker("volume", "rm", volume.name)
            self._report(volume, was_present=present)
        self._remove_image()

    def _remove_image(self) -> None:
        image = self._targets(Step.DOCKER, Kind.IMAGE)[0]
        references = self._docker_names("image", "{{.Repository}}:{{.Tag}}")
        present = image.name in references
        if present:
            self._docker("image", "rm", "--no-prune", image.name)
        self._report(image, was_present=present)
        others = [
            reference
            for reference in references
            if reference.startswith(ADOPTION_IMAGE_PREFIX) and reference != image.name
        ]
        if others:
            self.say(f"left: {len(others)} other {ADOPTION_IMAGE_PREFIX[:-1]} image(s)")

    def _project_containers(self) -> list[str]:
        return self._lines_or_fail(PROJECT_CONTAINERS)

    def _docker_names(self, noun: str, template: str) -> list[str]:
        return self._lines_or_fail(("docker", noun, "ls", "--format", template))

    def _docker(self, *arguments: str) -> None:
        command = ("docker", *arguments)
        outcome = self.run(command)
        if outcome.code != 0:
            raise StepFailedError(*command[:2], f"exit {outcome.code}")

    def _step_folders(self) -> None:
        was_present = exists(self.live) or exists(self.retiring)
        if exists(self.live):
            self.live.rename(self.retiring)
        if exists(self.retiring):
            shutil.rmtree(self.retiring)
        self._report(LIVE_TARGET, was_present=was_present)
        for target in self._targets(Step.FOLDERS, Kind.TREE):
            if target is not LIVE_TARGET:
                self._remove_path(target)


def retire(  # noqa: PLR0913, PLR0917 - every collaborator is a test seam
    home: Path,
    checkout: Path,
    run: Runner,
    say: Say,
    effective_uid: int,
    passwd_home: Path,
) -> int:
    """Refuse, or retire the listed targets; exit code 0, 1 failed, 2 refused."""
    retirement = Retirement(home, checkout, run, say)
    reason = retirement.refusal(effective_uid, passwd_home)
    if reason is not None:
        say(f"refused: {reason}")
        return 2
    return retirement.execute()


class RefusedError(Exception):
    """The probe run must not probe; the reason names no value."""


@dataclass(frozen=True)
class Deployment:
    """What the checkout's compose file says the probes compare with."""

    lobby_origin: str
    talk_origin: str
    trusted_proxies: str
    network: str


def _single_value(compose: str, key: str) -> str:
    found = re.findall(rf"^\s*{re.escape(key)}:\s*(\S+)\s*$", compose, re.MULTILINE)
    if len(found) != 1:
        reason = f"compose.yaml has not exactly one {key} line"
        raise RefusedError(reason)
    return found[0].strip("\"'")


def deployment_of(compose: str) -> Deployment:
    """Read the four compared values from compose.yaml, one line each."""
    return Deployment(
        lobby_origin=_single_value(compose, "PRESENTATOR_PUBLIC_ORIGIN"),
        talk_origin=_single_value(compose, "PRESENTATOR_TALK_ORIGIN"),
        trusted_proxies=_single_value(compose, "PRESENTATOR_TRUSTED_PROXIES"),
        network=_single_value(compose, "traefik.docker.network"),
    )


class Verdict(enum.Enum):
    """How one probe ended; the value is the word that starts its line."""

    HELD = "held"
    FAILED = "failed"
    NOT_EXERCISED = "not exercised"


class Side(enum.Enum):
    """The two hosts one instance answers on."""

    LOBBY = "lobby"
    TALK = "talk"


class Vantage(enum.Enum):
    """Where a request enters: through Traefik on this machine or from outside."""

    LOCAL = "local"
    PUBLIC = "public"


@dataclass(frozen=True)
class Answer:
    """What curl got: its exit code, the status and the lower-cased headers."""

    curl_code: int
    status: int
    headers: dict[str, list[str]]

    def first(self, name: str) -> str:
        """The first value of a header, or an empty string."""
        return next(iter(self.headers.get(name, [])), "")


def _answer_of(outcome: Outcome) -> Answer:
    head = outcome.text.replace("\r\n", "\n").partition("\n\n")[0]
    status_line, *header_lines = head.splitlines() or [""]
    words = status_line.split()
    headers: dict[str, list[str]] = {}
    for line in header_lines:
        name, _, value = line.partition(":")
        headers.setdefault(name.strip().lower(), []).append(value.strip())
    code = words[1] if len(words) > 1 else ""
    status = int(code) if len(code) == 3 and code.isascii() and code.isdecimal() else 0  # noqa: PLR2004
    return Answer(outcome.code, status, headers)


def _held_if(check: Callable[[], bool]) -> Verdict:
    return Verdict.HELD if check() else Verdict.FAILED


def _redirects_to_sign_in(answer: Answer, side: Side, deployment: Deployment) -> bool:
    location = answer.first("location")
    if side is Side.TALK:
        return answer.status == 303 and location == deployment.lobby_origin  # noqa: PLR2004
    path = urlsplit(location).path
    return 300 <= answer.status < 400 and path == SIGN_IN_PATH  # noqa: PLR2004


def _root_holds(answer: Answer, side: Side, deployment: Deployment) -> bool:
    hardened = all(name in answer.headers for name in HARDENING_HEADERS)
    return hardened and _redirects_to_sign_in(answer, side, deployment)


def _sets_only_secure_host_cookies(answer: Answer, *_: object) -> bool:
    cookies = answer.headers.get("set-cookie", [])
    return bool(cookies) and all(
        cookie.startswith(HOST_COOKIE_PREFIX)
        and "secure" in {part.strip().lower() for part in cookie.split(";")[1:]}
        for cookie in cookies
    )


def _answers_ok(answer: Answer, *_: object) -> bool:
    return answer.status == 200  # noqa: PLR2004


@dataclass(frozen=True)
class WebProbe:
    """One request and the judgement of its answer, on the sides it applies to."""

    label: str
    path: str
    sides: tuple[Side, ...]
    holds: Callable[[Answer, Side, Deployment], bool]


WEB_PROBES = (
    WebProbe("root", "/", (Side.LOBBY, Side.TALK), _root_holds),
    WebProbe(
        "sign-in",
        SIGN_IN_PATH,
        (Side.LOBBY, Side.TALK),
        _sets_only_secure_host_cookies,
    ),
    WebProbe("health", HEALTH_PATH, (Side.LOBBY, Side.TALK), _answers_ok),
)


class Inspection(Retirement):
    """The probe run: it only reads, through the retire's git helper and runner."""

    def probe(self) -> int:
        """Probe the deployed instance; exit code 0 held, 1 failed, 2 refused."""
        try:
            record = self._deployed_record()
            if record is None:
                self.say("not deployed yet: run hal apply")
                return 0
            self._require_head(record)
            deployment = self._deployment()
        except RefusedError as reason:
            self.say(f"refused: {reason}")
            return 2
        verdicts = [
            self._reported(name, self._judged(check))
            for name, check in self._probes(deployment)
        ]
        if Verdict.FAILED in verdicts:
            self.say(STOP_NEXT_STEP)
            return 1
        return 0

    @staticmethod
    def _judged(check: Callable[[], Verdict]) -> Verdict | Exception:
        """Run one probe; whatever it raises is that probe's failure, nothing more."""
        try:
            return check()
        except Exception as failure:  # noqa: BLE001 - one probe never ends the run
            return failure

    def _reported(self, name: str, verdict: Verdict | Exception) -> Verdict:
        if isinstance(verdict, Exception):
            self.say(f"{Verdict.FAILED.value}: {name} ({type(verdict).__name__})")
            return Verdict.FAILED
        self.say(f"{verdict.value}: {name}")
        return verdict

    def _deployed_record(self) -> tuple[str, str] | None:
        record = self.home / DEPLOYED_RECORD
        if not record.parent.is_dir():
            raise RefusedError(RECORDS_MISSING)
        return self._read_record(record) if exists(record) else None

    def _read_record(self, record: Path) -> tuple[str, str]:
        unreadable = RefusedError(RECORD_UNREADABLE)
        try:
            data = cast("object", json.loads(record.read_text()))
        except (OSError, ValueError, RecursionError) as failure:
            raise unreadable from failure
        if not isinstance(data, dict):
            raise unreadable
        fields = cast("dict[str, object]", data)
        tag, sha = fields.get("tag"), fields.get("sha")
        if not isinstance(tag, str) or not isinstance(sha, str):
            raise unreadable
        return tag, sha

    def _require_head(self, record: tuple[str, str]) -> None:
        tag, sha = record
        head = self._git(self.checkout, "rev-parse", "HEAD")
        tags = self._git(self.checkout, "tag", "--list", "v*", "--points-at", "HEAD")
        if head != [sha] or tag not in (tags or []):
            raise RefusedError(NOT_HEAD)

    def _deployment(self) -> Deployment:
        try:
            compose = (self.checkout / "compose.yaml").read_text()
        except OSError, ValueError:
            reason = "compose.yaml cannot be read"
            raise RefusedError(reason) from None
        return deployment_of(compose)

    def _probes(
        self, deployment: Deployment
    ) -> Iterator[tuple[str, Callable[[], Verdict]]]:
        yield from self._web_probes(Vantage.LOCAL, deployment)
        yield from self._container_probes(deployment)
        yield from self._web_probes(Vantage.PUBLIC, deployment)

    def _web_probes(
        self, vantage: Vantage, deployment: Deployment
    ) -> Iterator[tuple[str, Callable[[], Verdict]]]:
        for probe in WEB_PROBES:
            for side in probe.sides:
                name = f"{vantage.value} {side.value} {probe.label}"
                yield name, partial(self._web_verdict, vantage, probe, side, deployment)

    def _web_verdict(
        self, vantage: Vantage, probe: WebProbe, side: Side, deployment: Deployment
    ) -> Verdict:
        answer = self._fetch(vantage, side, probe.path, deployment)
        if vantage is Vantage.PUBLIC and answer.curl_code in DARK_CURL_EXITS:
            return Verdict.NOT_EXERCISED
        held = answer.curl_code == 0 and probe.holds(answer, side, deployment)
        return Verdict.HELD if held else Verdict.FAILED

    def _fetch(
        self, vantage: Vantage, side: Side, path: str, deployment: Deployment
    ) -> Answer:
        origin = (
            deployment.lobby_origin if side is Side.LOBBY else deployment.talk_origin
        )
        command = ["curl", "--silent", "--include", "--max-time", "5"]
        if vantage is Vantage.LOCAL:
            command += ["--header", f"Host: {urlsplit(origin).hostname}"]
            origin = TRAEFIK_URL.removesuffix("/")
        return _answer_of(self.run((*command, origin + path)))

    def _container_probes(
        self, deployment: Deployment
    ) -> Iterator[tuple[str, Callable[[], Verdict]]]:
        web: list[str] = []

        def one_web_container() -> Verdict:
            found = self._lines(WEB_CONTAINER)
            web[:] = found if found is not None and len(found) == 1 else []
            return Verdict.HELD if web else Verdict.FAILED

        yield "web container", one_web_container
        if not web:
            return
        container = web[0]
        checks = (
            ("mounts", lambda: self._mounts_clean(container)),
            ("groups", lambda: self._unset(container, "GroupAdd", [])),
            ("ports", lambda: self._unset(container, "PortBindings", {})),
            ("secrets", lambda: self._secrets_held(container)),
            ("gateway", lambda: self._gateway_is_proxy(deployment)),
        )
        for name, check in checks:
            yield f"web container {name}", partial(_held_if, check)

    def _json(self, arguments: Sequence[str]) -> object:
        outcome = self.run(arguments)
        if outcome.code != 0:
            raise ValueError(UNEXPECTED_ANSWER)
        return cast("object", json.loads(outcome.text))

    def _inspected(self, container: str, field: str) -> object:
        template = f"{{{{json {field}}}}}"
        return self._json(("docker", "inspect", "--format", template, container))

    def _unset(self, container: str, field: str, empty: object) -> bool:
        answer = self._inspected(container, f".HostConfig.{field}")
        return answer is None or (type(answer) is type(empty) and not answer)

    def _mounts(self, container: str) -> list[dict[str, object]]:
        mounts = self._inspected(container, ".Mounts")
        if not isinstance(mounts, list):
            raise ValueError(UNEXPECTED_ANSWER)  # noqa: TRY004 - one handled kind
        return cast("list[dict[str, object]]", mounts)

    def _mounts_clean(self, container: str) -> bool:
        return not any(
            "docker.sock" in f"{mount.get('Source')}{mount.get('Destination')}"
            for mount in self._mounts(container)
        )

    def _secrets_held(self, container: str) -> bool:
        secrets = [
            mount
            for mount in self._mounts(container)
            if str(mount.get("Destination")).startswith(SECRETS_MOUNT)
        ]
        folder = self.home / SECRETS_FOLDER
        return len(secrets) == 2 and all(  # noqa: PLR2004
            mount.get("Type") == "bind"
            and mount.get("RW") is False
            and Path(str(mount.get("Source"))).is_relative_to(folder)
            for mount in secrets
        )

    def _gateway_is_proxy(self, deployment: Deployment) -> bool:
        pools = self._json(
            (
                "docker",
                "network",
                "inspect",
                "--format",
                "{{json .IPAM.Config}}",
                deployment.network,
            )
        )
        if not isinstance(pools, list):
            raise ValueError(UNEXPECTED_ANSWER)  # noqa: TRY004 - one handled kind
        return any(
            isinstance(pool, dict)
            and cast("dict[str, object]", pool).get("Gateway")
            == deployment.trusted_proxies
            for pool in cast("list[object]", pools)
        )


def cut_over(  # noqa: PLR0913, PLR0917 - every collaborator is a test seam
    home: Path,
    checkout: Path,
    run: Runner,
    say: Say,
    effective_uid: int,
    passwd_home: Path,
) -> int:
    """Retire; only a finished retire is followed by the probe run."""
    code = retire(home, checkout, run, say, effective_uid, passwd_home)
    if code != 0:
        return code
    return Inspection(home, checkout, run, say).probe()


def main() -> int:
    """Cut over against the real home, the script's checkout and the real programs."""
    effective_uid = os.geteuid()
    return cut_over(
        home=Path.home(),
        checkout=Path(__file__).resolve().parents[1],
        run=real_runner,
        say=print,
        effective_uid=effective_uid,
        passwd_home=Path(pwd.getpwuid(effective_uid).pw_dir),
    )


if __name__ == "__main__":
    sys.exit(main())
