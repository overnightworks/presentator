"""The cutover command retires exactly its listed targets, in order, and resumes."""

import importlib
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
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
UNIT_EXIT_CODES = {"stop": 5, "disable": 1, "reset-failed": 1}


@dataclass
class Host:
    """Docker, systemd, git and Traefik as one mutable fake behind the runner."""

    home: Path
    calls: list[tuple[str, ...]] = field(default_factory=list[tuple[str, ...]])
    containers: list[str] = field(default_factory=lambda: ["c0ffee"])
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
    failed_units: set[str] = field(default_factory=set[str])
    checkout_on_tag: bool = True
    checkout_status: str = ""
    live_remotes: str = "origin"
    live_origin: str = ORIGIN
    live_head: str = LIVE_HEAD
    live_status: str = "?? .env.bak-preupgrade\n?? compose.override.yaml"
    live_refs: str = (
        f"{LIVE_HEAD} refs/heads/main\n"
        f"{'b' * 40} refs/remotes/origin/HEAD\n"
        f"{'c' * 40} refs/remotes/origin/claude/issue-1-x"
    )

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
        return outcome(0 if self.traefik_answers else 7, "")

    def run_git(self, rest: tuple[str, ...]) -> object:
        path, words = Path(rest[1]), rest[2:]
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
            case ("ps", *_):
                return outcome(0, "\n".join(self.containers))
            case ("stop", _):
                return outcome(0, "")
            case ("rm", container):
                self.containers.remove(container)
            case ("network", "ls", *_):
                return outcome(0, "\n".join(self.networks))
            case ("network", "rm", name):
                self.networks.remove(name)
            case ("volume", "ls", *_):
                return outcome(0, "\n".join(self.volumes))
            case ("volume", "rm", name):
                self.volumes.remove(name)
            case ("image", "ls", *_):
                return outcome(0, "\n".join(self.images))
            case ("image", "rm", reference):
                self.images.remove(reference)
            case _:
                pass
        return outcome(0, "")

    def run_systemctl(self, rest: tuple[str, ...]) -> object:
        assert rest[0] == "--user"
        verb, unit = rest[1], rest[-1]
        known = (self.home / UNIT_DIRECTORY / unit).exists()
        if verb == "show":
            load = "loaded" if known else "not-found"
            active = "failed" if unit in self.failed_units else "inactive"
            return outcome(0, f"LoadState={load}\nActiveState={active}\n")
        if verb == "reset-failed" and unit in self.failed_units:
            self.failed_units.discard(unit)
            return outcome(0, "")
        if verb in UNIT_EXIT_CODES and not known:
            return outcome(UNIT_EXIT_CODES[verb], "")
        return outcome(0, "")

    def commands(self, program: str) -> list[tuple[str, ...]]:
        return [call for call in self.calls if call[0] == program]


@pytest.fixture
def cutover(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "path", [str(SCRIPTS), *sys.path])
    return importlib.import_module("cutover")


def outcome(code: int, text: str) -> object:
    return importlib.import_module("cutover").Outcome(code, text)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    for name in (*LISTED_PATHS[1:4], *SURVIVORS):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "data").write_text("x")
    for name in LISTED_PATHS[4:]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("unit")
    live = root / "presentator-live"
    (live / ".git").mkdir(parents=True)
    (live / "speech").mkdir()
    (live / "speech" / "inside-link").symlink_to(root / "presentator-stage")
    (root / "presentator-backups" / "inside-link").symlink_to(
        root / ".cache" / "huggingface"
    )
    return root


@pytest.fixture
def host(home: Path) -> Host:
    return Host(home)


@dataclass
class Run:
    """The exit code and the printed lines of one run."""

    code: int
    lines: list[str]


@pytest.fixture
def execute(
    cutover: ModuleType, home: Path, host: Host, tmp_path: Path
) -> Callable[..., Run]:
    def go(uid: int = 1000, passwd_home: Path | None = None) -> Run:
        lines: list[str] = []
        code = cutover.retire(
            home=home,
            checkout=tmp_path / "checkout",
            run=host,
            say=lines.append,
            effective_uid=uid,
            passwd_home=passwd_home or home,
        )
        return Run(code, lines)

    return go


def present(home: Path, names: Sequence[str]) -> list[str]:
    return [name for name in names if (home / name).exists()]


def test_a_run_removes_exactly_the_list_and_keeps_every_survivor(
    execute: Callable[..., Run], host: Host, home: Path
) -> None:
    result = execute()

    assert result.code == 0
    assert present(home, LISTED_PATHS) == []
    assert not (home / "presentator-live.retiring").exists()
    assert not (home / f"{UNIT_DIRECTORY}/presentator-copresenter.service.d").exists()
    assert present(home, SURVIVORS) == list(SURVIVORS)
    assert (home / "presentator-stage").is_dir()
    assert host.containers == []
    assert host.networks == ["presentator_default"]
    assert host.volumes == list(KEPT_VOLUMES)
    assert host.images == ["presentator-adoption-other:x", "presentator:v1"]
    assert "left: presentator-adoption-other:x" in result.lines


def test_the_commands_never_use_compose_force_or_a_shell(
    execute: Callable[..., Run], host: Host
) -> None:
    execute()

    docker = host.commands("docker")
    assert ("docker", "stop", "c0ffee") in docker
    assert ("docker", "rm", "c0ffee") in docker
    assert not any(
        "compose" in call or "-f" in call or "--force" in call for call in docker
    )
    assert {call[0] for call in host.calls} <= {"git", "docker", "systemctl", "curl"}


def test_the_steps_run_in_the_fixed_order(
    execute: Callable[..., Run], host: Host
) -> None:
    execute()

    flat = [" ".join(call[:3]) for call in host.calls]
    first = {
        "reload": flat.index("systemctl --user daemon-reload"),
        "rm container": flat.index("docker rm c0ffee"),
        "network": flat.index("docker network rm"),
        "volume": flat.index("docker volume rm"),
        "image": flat.index("docker image rm"),
    }
    assert list(first.values()) == sorted(first.values())


def test_a_second_run_reports_every_target_as_already_gone_and_exits_zero(
    execute: Callable[..., Run],
) -> None:
    execute()
    second = execute()

    assert second.code == 0
    assert all(line.startswith(("already gone", "left:")) for line in second.lines)
    assert len(second.lines) == 14 + 1


@pytest.mark.parametrize(
    ("breakage", "reason"),
    [
        ({"checkout_on_tag": False}, "not the commit of a v* tag"),
        ({"checkout_status": " M x"}, "uncommitted"),
        ({"live_origin": "https://example.org/x.git"}, "another origin"),
        ({"live_remotes": "origin\nfork"}, "remote other than origin"),
        ({"live_head": "e" * 40}, "HEAD is not 1f0422c"),
        ({"live_status": "?? compose.override.yaml"}, "changes beyond"),
        (
            {"live_status": " M x\n?? .env.bak-preupgrade\n?? compose.override.yaml"},
            "changes beyond",
        ),
        (
            {"live_refs": f"{LIVE_HEAD} refs/heads/main\n{'b' * 40} refs/stash"},
            "ref beyond",
        ),
        (
            {"live_refs": f"{LIVE_HEAD} refs/heads/main\n{'b' * 40} refs/tags/t"},
            "ref beyond",
        ),
        ({"live_refs": f"{'b' * 40} refs/heads/main"}, "ref beyond"),
        ({"containers": [], "traefik_answers": False}, "not this machine"),
    ],
    ids=[
        "checkout-not-on-tag",
        "checkout-dirty",
        "origin",
        "second-remote",
        "head",
        "missing-untracked",
        "modified-file",
        "stash",
        "tag",
        "branch-off-main",
        "other-machine",
    ],
)
def test_a_refusal_changes_nothing_and_names_its_reason(
    execute: Callable[..., Run],
    host: Host,
    home: Path,
    breakage: dict[str, object],
    reason: str,
) -> None:
    for attribute, value in breakage.items():
        setattr(host, attribute, value)
    before = sorted(str(p) for p in home.rglob("*"))

    result = execute()

    assert result.code != 0
    assert len(result.lines) == 1
    assert result.lines[0].startswith("refused:")
    assert reason in result.lines[0]
    assert sorted(str(p) for p in home.rglob("*")) == before
    assert host.commands("systemctl") == []
    assert [call for call in host.commands("docker") if call[1] != "ps"] == []


def test_a_root_user_is_refused(execute: Callable[..., Run]) -> None:
    result = execute(uid=0)

    assert result.code != 0
    assert "root" in result.lines[0]


def test_a_home_that_differs_from_the_password_database_is_refused(
    execute: Callable[..., Run], tmp_path: Path
) -> None:
    result = execute(passwd_home=tmp_path / "elsewhere")

    assert result.code != 0
    assert "password database" in result.lines[0]


def test_a_missing_live_directory_is_not_a_git_checkout_refusal(
    execute: Callable[..., Run], home: Path
) -> None:
    (home / "presentator-live" / ".git").rmdir()

    assert "not a git checkout" in execute().lines[0]


def test_both_live_and_retiring_present_is_refused(
    execute: Callable[..., Run], home: Path
) -> None:
    (home / "presentator-live.retiring").mkdir()

    result = execute()

    assert "both" in result.lines[0]
    assert (home / "presentator-live").exists()


def test_a_listed_path_that_is_a_link_out_of_its_place_is_refused(
    execute: Callable[..., Run], home: Path
) -> None:
    state = home / ".local/state/presentator-speech"
    (state / "data").unlink()
    state.rmdir()
    state.symlink_to(home / "presentator-stage")

    result = execute()

    assert "resolves outside" in result.lines[0]
    assert (home / "presentator-stage" / "keep").exists()


def test_a_tree_holding_a_link_to_a_survivor_deletes_the_link_not_the_survivor(
    execute: Callable[..., Run], home: Path
) -> None:
    execute()

    assert (home / "presentator-stage" / "keep" / "data").exists()
    assert (home / ".cache/huggingface/keep/data").exists()


@pytest.mark.parametrize(
    "failing",
    [
        "docker network rm",
        "docker volume rm",
        "docker image rm",
        "systemctl --user stop",
    ],
)
def test_a_failed_step_names_the_step_stops_and_the_next_run_resumes(
    execute: Callable[..., Run], host: Host, home: Path, failing: str
) -> None:
    host.failing.add(failing)

    failed = execute()

    assert failed.code == 1
    assert failed.lines[-1].startswith("failed: ")
    assert (home / "presentator-live").exists()
    assert (home / "presentator-backups").exists()

    host.failing.clear()
    resumed = execute()

    assert resumed.code == 0
    assert present(home, LISTED_PATHS) == []


def test_an_os_error_inside_a_step_names_the_error_class(
    execute: Callable[..., Run], home: Path
) -> None:
    drop_in = home / UNIT_DIRECTORY / "presentator-copresenter.service.d"
    (drop_in / "extra.conf").write_text("x")

    result = execute()

    assert result.code == 1
    assert result.lines[-1].endswith("OSError")
    assert (home / "presentator-live").exists()


@pytest.mark.parametrize(
    "start",
    ["retiring-only", "units-gone-before-reload", "half-deleted-retiring"],
)
def test_a_run_resumes_from_each_start_state(
    execute: Callable[..., Run], host: Host, home: Path, start: str
) -> None:
    if start == "retiring-only":
        (home / "presentator-live").rename(home / "presentator-live.retiring")
    if start == "half-deleted-retiring":
        (home / "presentator-live").rename(home / "presentator-live.retiring")
        shutil.rmtree(home / "presentator-live.retiring" / "speech")
    if start == "units-gone-before-reload":
        for name in LISTED_PATHS[4:]:
            (home / name).unlink()
        (home / f"{UNIT_DIRECTORY}/presentator-copresenter.service.d").rmdir()

    result = execute()

    assert result.code == 0
    assert present(home, LISTED_PATHS) == []
    assert not (home / "presentator-live.retiring").exists()
    assert ("systemctl", "--user", "daemon-reload") in host.calls


def test_a_crash_looped_unit_is_reset_after_the_reload(
    execute: Callable[..., Run], host: Host
) -> None:
    host.failed_units.add("presentator-speech.service")

    result = execute()

    assert result.code == 0
    reset = ("systemctl", "--user", "reset-failed", "presentator-speech.service")
    assert reset in host.calls
    assert host.calls.index(reset) > host.calls.index(
        ("systemctl", "--user", "daemon-reload")
    )


def test_a_unit_that_stays_known_fails_the_unit_step(
    execute: Callable[..., Run], host: Host, home: Path
) -> None:
    host.failed_units.add("presentator-speech.service")
    host.failing.add("systemctl --user reset-failed")

    result = execute()

    assert result.code == 1
    assert "units" in result.lines[-1]
    assert (home / "presentator-live").exists()


def test_the_output_carries_no_value_from_docker_systemd_or_git(
    execute: Callable[..., Run], host: Host
) -> None:
    host.containers = ["secretid"]
    result = execute()

    assert "secretid" not in "\n".join(result.lines)
    assert LIVE_HEAD not in "\n".join(result.lines)


def test_the_command_reads_no_env_file_and_calls_no_github(
    execute: Callable[..., Run], host: Host
) -> None:
    execute()

    assert not any(
        ".env" in part or call[0] == "gh" for call in host.calls for part in call
    )
    assert not any(
        "github" in part and call[0] != "git" for call in host.calls for part in call
    )


def test_the_real_runner_returns_code_and_text(cutover: ModuleType) -> None:
    result = cutover.real_runner((sys.executable, "-c", "print('hi')"))

    assert (result.code, result.text.strip()) == (0, "hi")


def test_main_refuses_when_the_script_checkout_is_not_on_a_tag(
    cutover: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    refused: list[str] = []

    def refuse_every_program(_arguments: Sequence[str]) -> object:
        return cutover.Outcome(1, "")

    monkeypatch.setattr(cutover, "real_runner", refuse_every_program)
    monkeypatch.setattr(cutover, "print", refused.append, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cutover.main() == REFUSED
