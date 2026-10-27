"""One-time command that retires the old `presentator-live` instance.

Run once as `python3 scripts/cutover.py`, from the checkout on a `v*` tag,
after `hal setup --apply` and before edge enables presentator's catalog entry
(#508 S9l). It removes exactly the listed targets in a fixed order (units,
containers, volumes and image, folders), refuses before any change when the
host or the checkout is not what the list assumes, stops at the first failed
step and resumes on the next run; a second run reports every target as
"already gone". It prints one line per target (surviving
`presentator-adoption-*` images are only counted, as ruled) and no value that
Docker, systemd or git produced, and reads no `.env`. The operator's rulings on
#508 lines 1, 2, 3 and 6 are the contract; #508 S9m deletes this file.
"""

import enum
import os
import pwd
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    finished = subprocess.run(  # no shell: the argument vector is fixed
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
                        unit, "stays enabled", f"disable exit {disabled.code}"
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
            self._docker("image", "rm", image.name)
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


def main() -> int:
    """Retire against the real home, the script's checkout and the real programs."""
    effective_uid = os.geteuid()
    return retire(
        home=Path.home(),
        checkout=Path(__file__).resolve().parents[1],
        run=real_runner,
        say=print,
        effective_uid=effective_uid,
        passwd_home=Path(pwd.getpwuid(effective_uid).pw_dir),
    )


if __name__ == "__main__":
    sys.exit(main())
