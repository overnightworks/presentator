"""The cutover command retires exactly its listed targets, in order, and resumes."""

import os
import shutil
import stat
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.cutover_host import (
    IMAGE,
    KEPT_VOLUMES,
    LISTED_PATHS,
    LIVE_HEAD,
    REFUSED,
    SURVIVORS,
    UNIT_DIRECTORY,
    UNITS,
    VOLUMES,
    Host,
)


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


UNIT_STEP = "failed: units"
DOCKER_STEP = "failed: containers, volumes and image"
RELOAD = ("systemctl", "--user", "daemon-reload")
Arrange = Callable[[Host, Path], dict[str, object]]


def test_a_run_removes_exactly_the_list_and_keeps_every_survivor(
    execute: Callable[..., Run], host: Host, home: Path
) -> None:
    """S9h1 (#542) sentence 1: exactly the list goes, every survivor stays."""
    result = execute()

    assert result.code == 0
    assert present(home, LISTED_PATHS) == []
    assert not (home / "presentator-live.retiring").exists()
    assert not (home / f"{UNIT_DIRECTORY}/presentator-copresenter.service.d").exists()
    assert present(home, SURVIVORS) == list(SURVIVORS)
    assert (home / "presentator-stage").is_dir()
    assert list(host.containers) == ["5afe01", "b10b02"]
    assert host.networks == ["presentator_default"]
    assert host.volumes == list(KEPT_VOLUMES)
    assert host.images == ["presentator-adoption-other:x", "presentator:v1"]
    assert "left: 1 other presentator-adoption image(s)" in result.lines
    assert "presentator-adoption-other" not in "\n".join(result.lines)
    assert result.lines.count("removed: presentator-live") == 1


def test_the_commands_never_use_compose_force_or_a_shell(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentences 1 and 8: no compose, no -f, no shell."""
    execute()

    docker = host.commands("docker")
    assert ("docker", "stop", "c0ffee") in docker
    assert ("docker", "rm", "c0ffee") in docker
    assert ("docker", "image", "rm", "--no-prune", IMAGE) in docker
    assert (
        "docker",
        "ps",
        "--all",
        "--quiet",
        "--filter",
        "label=com.docker.compose.project=presentator-live",
    ) in docker
    stopped_or_removed = {call[2] for call in docker if call[1] in {"stop", "rm"}}
    assert stopped_or_removed == {"c0ffee"}
    assert not any(
        "compose" in call or "-f" in call or "--force" in call for call in docker
    )
    assert {call[0] for call in host.calls} <= {"git", "docker", "systemctl", "curl"}


def test_every_git_call_takes_no_optional_lock(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 5: the refusal phase writes nothing, not even an index."""
    execute()

    assert all("--no-optional-locks" in call for call in host.commands("git"))


def test_the_steps_run_in_the_fixed_order(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 2: units, containers, volumes, image, then folders."""
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


def test_a_unit_is_stopped_and_disabled_only_while_its_file_exists(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 2: stop and disable both units, before their files go."""
    execute()
    execute()

    assert host.stopped_or_disabled == [
        ("stop", UNITS[0]),
        ("disable", UNITS[0]),
        ("stop", UNITS[1]),
        ("disable", UNITS[1]),
    ]


def test_a_second_run_reports_every_target_as_already_gone_and_exits_zero(
    execute: Callable[..., Run],
) -> None:
    """S9h1 (#542) sentence 3: a second run removes nothing and exits 0."""
    execute()
    second = execute()

    assert second.code == 0
    assert all(line.startswith(("already gone", "left:")) for line in second.lines)
    assert len(second.lines) == 14 + 1


def host_has(**attributes: object) -> Arrange:
    def arrange(host: Host, _home: Path) -> dict[str, object]:
        for attribute, value in attributes.items():
            setattr(host, attribute, value)
        return {}

    return arrange


def without_git_folder(_host: Host, home: Path) -> dict[str, object]:
    (home / "presentator-live" / ".git").rmdir()
    return {}


def with_retiring_folder(_host: Host, home: Path) -> dict[str, object]:
    (home / "presentator-live.retiring").mkdir()
    return {}


def with_a_link_out_of_its_place(_host: Host, home: Path) -> dict[str, object]:
    state = home / ".local/state/presentator-speech"
    (state / "data").unlink()
    state.rmdir()
    state.symlink_to(home / "presentator-stage")
    return {}


def as_root(_host: Host, _home: Path) -> dict[str, object]:
    return {"uid": 0}


def with_another_home(_host: Host, home: Path) -> dict[str, object]:
    return {"passwd_home": home.parent / "elsewhere"}


def with_refs(*refs: str) -> Arrange:
    return host_has(live_refs="\n".join([f"{LIVE_HEAD} refs/heads/main", *refs]))


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        (host_has(checkout_on_tag=False), "not the commit of a v* tag"),
        (host_has(checkout_status=" M x"), "uncommitted"),
        (as_root, "root"),
        (with_another_home, "password database"),
        (host_has(containers={}, traefik_answers=False), "not this machine"),
        (without_git_folder, "not a git checkout"),
        (host_has(live_origin="https://example.org/x.git"), "another origin"),
        (host_has(live_remotes="origin\nfork"), "remote other than origin"),
        (host_has(live_head="e" * 40), "HEAD is not 1f0422c"),
        (host_has(live_status="?? compose.override.yaml"), "changes beyond"),
        (
            host_has(
                live_status=" M x\n?? .env.bak-preupgrade\n?? compose.override.yaml"
            ),
            "changes beyond",
        ),
        (with_refs(f"{'b' * 40} refs/stash"), "ref beyond"),
        (with_refs(f"{'b' * 40} refs/tags/t"), "ref beyond"),
        (with_refs(f"{'b' * 40} refs/notes/commits"), "ref beyond"),
        (with_refs(f"{LIVE_HEAD} refs/heads/other"), "ref beyond"),
        (with_refs(f"{'b' * 40} refs/remotes/fork/x"), "ref beyond"),
        (host_has(live_refs=f"{'b' * 40} refs/heads/main"), "ref beyond"),
        (with_retiring_folder, "both"),
        (with_a_link_out_of_its_place, "resolves outside"),
    ],
    ids=[
        "checkout-not-on-tag",
        "checkout-dirty",
        "root",
        "home-differs",
        "other-machine",
        "not-a-git-checkout",
        "origin",
        "second-remote",
        "head",
        "missing-untracked",
        "modified-file",
        "stash",
        "tag",
        "note",
        "second-branch",
        "remote-tracking-ref-of-another-remote",
        "branch-off-main",
        "both-paths",
        "link-out",
    ],
)
def test_a_refusal_changes_nothing_and_names_its_reason(
    execute: Callable[..., Run],
    host: Host,
    home: Path,
    arrange: Arrange,
    reason: str,
) -> None:
    """S9h1 (#542) sentence 5: each refusal names its reason and changes nothing."""
    arguments = arrange(host, home)
    before = sorted(str(p) for p in home.rglob("*"))

    result = execute(**arguments)

    assert result.code == REFUSED
    assert len(result.lines) == 1
    assert result.lines[0].startswith("refused:")
    assert reason in result.lines[0]
    assert sorted(str(p) for p in home.rglob("*")) == before
    assert host.commands("systemctl") == []
    assert [call for call in host.commands("docker") if call[1] != "ps"] == []


def test_a_tree_holding_a_link_to_a_survivor_deletes_the_link_not_the_survivor(
    execute: Callable[..., Run], home: Path
) -> None:
    """S9h1 (#542) sentence 2: a deleted tree is never left through a link."""
    execute()

    assert (home / "presentator-stage" / "keep" / "data").exists()
    assert (home / ".cache/huggingface/keep/data").exists()


@pytest.mark.parametrize(
    ("failing", "failure_line"),
    [
        ("docker network rm", f"{DOCKER_STEP}: docker network exit 1"),
        ("docker volume rm", f"{DOCKER_STEP}: docker volume exit 1"),
        ("docker image rm", f"{DOCKER_STEP}: docker image exit 1"),
        ("docker network ls", f"{DOCKER_STEP}: docker network exit 1"),
        ("systemctl --user stop", f"{UNIT_STEP}: {UNITS[0]} stays active stop exit 1"),
    ],
)
def test_a_failed_step_names_the_step_stops_and_the_next_run_resumes(
    execute: Callable[..., Run],
    host: Host,
    home: Path,
    failing: str,
    failure_line: str,
) -> None:
    """S9h1 (#542) sentence 4: a failed step prints step and exit code, then resumes."""
    host.failing.add(failing)

    failed = execute()

    assert failed.code == 1
    assert failed.lines[-1] == failure_line
    assert (home / "presentator-live").exists()
    assert (home / "presentator-backups").exists()

    host.failing.clear()
    resumed = execute()

    assert resumed.code == 0
    assert present(home, LISTED_PATHS) == []


def test_a_unit_that_stays_enabled_fails_the_step_and_a_resume_completes(
    execute: Callable[..., Run], host: Host, home: Path
) -> None:
    """S9h1 (#542) sentences 2 and 4: a unit still enabled keeps its file and link."""
    host.stuck_enabled.add(UNITS[0])

    failed = execute()

    assert failed.code == 1
    assert failed.lines[-1] == f"{UNIT_STEP}: {UNITS[0]} not disabled disable exit 0"
    assert (home / UNIT_DIRECTORY / UNITS[0]).exists()
    assert host.wants_link(UNITS[0]).is_symlink()

    host.stuck_enabled.clear()
    resumed = execute()

    assert resumed.code == 0
    assert present(home, LISTED_PATHS) == []
    assert not host.wants_link(UNITS[0]).is_symlink()


@pytest.mark.parametrize(
    "failing",
    [
        "systemctl --user stop",
        "systemctl --user disable",
        "systemctl --user daemon-reload",
    ],
)
def test_a_systemctl_call_that_exits_non_zero_but_reaches_its_state_is_no_failure(
    execute: Callable[..., Run], host: Host, home: Path, failing: str
) -> None:
    """S9h1 (#542) sentence 2: systemctl is decided from state, not exit codes."""
    host.exits_after_acting.add(failing)

    result = execute()

    assert result.code == 0
    assert present(home, LISTED_PATHS) == []


@pytest.mark.parametrize("lingering", ["c0ffee", "presentator-live_default"])
def test_a_container_or_network_that_survives_its_removal_fails_the_docker_step(
    execute: Callable[..., Run], host: Host, home: Path, lingering: str
) -> None:
    """S9h1 (#542) sentence 1: the run confirms that no container or network is left."""
    host.lingering.add(lingering)

    result = execute()

    assert result.code == 1
    assert result.lines[-1].startswith(DOCKER_STEP)
    assert host.volumes == [*VOLUMES, *KEPT_VOLUMES]
    assert host.images[0] == IMAGE
    assert present(home, LISTED_PATHS[:4]) == list(LISTED_PATHS[:4])


def test_an_os_error_inside_a_step_names_the_error_class(
    execute: Callable[..., Run], home: Path
) -> None:
    """S9h1 (#542) sentence 4: an OSError prints the step and the error's class."""
    drop_in = home / UNIT_DIRECTORY / "presentator-copresenter.service.d"
    (drop_in / "extra.conf").write_text("x")

    result = execute()

    assert result.code == 1
    assert result.lines[-1] == f"{UNIT_STEP}: OSError"
    assert (home / "presentator-live").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_a_folder_that_cannot_be_deleted_stops_the_run_and_the_next_run_resumes(
    execute: Callable[..., Run], home: Path
) -> None:
    """S9h1 (#542) sentence 4: a failed folder deletion touches nothing further."""
    locked = home / "presentator-backups" / "locked"
    locked.mkdir()
    (locked / "data").write_text("x")
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)

    failed = execute()

    locked.chmod(stat.S_IRWXU)
    assert failed.code == 1
    assert failed.lines[-1] == "failed: folders: PermissionError"
    assert not (home / "presentator-live").exists()
    assert (home / "presentator-backups").exists()
    assert (home / ".local/state/agent-presentator").exists()
    assert (home / ".local/state/presentator-speech").exists()

    resumed = execute()

    assert resumed.code == 0
    assert present(home, LISTED_PATHS) == []


@pytest.mark.parametrize(
    "start",
    ["retiring-only", "units-gone-before-reload", "half-deleted-retiring"],
)
def test_a_run_resumes_from_each_start_state(
    execute: Callable[..., Run], host: Host, home: Path, start: str
) -> None:
    """S9h1 (#542) sentence 4: a half-deleted tree resumes via .retiring."""
    if start == "retiring-only":
        (home / "presentator-live").rename(home / "presentator-live.retiring")
    if start == "half-deleted-retiring":
        (home / "presentator-live").rename(home / "presentator-live.retiring")
        shutil.rmtree(home / "presentator-live.retiring" / "speech")
    if start == "units-gone-before-reload":
        host.active_units.clear()
        for name in LISTED_PATHS[4:]:
            (home / name).unlink()
        (home / f"{UNIT_DIRECTORY}/presentator-copresenter.service.d").rmdir()

    result = execute()

    assert result.code == 0
    assert present(home, LISTED_PATHS) == []
    assert not (home / "presentator-live.retiring").exists()
    assert RELOAD in host.calls


def test_a_reset_failed_call_is_made_only_for_a_unit_that_is_failed_after_the_reload(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 2: reset-failed follows the reload, failed units only."""
    host.failed_units.add(UNITS[0])

    result = execute()

    assert result.code == 0
    resets = [call for call in host.calls if "reset-failed" in call]
    assert resets == [("systemctl", "--user", "reset-failed", UNITS[0])]
    assert host.calls.index(resets[0]) > host.calls.index(RELOAD)


def test_no_unit_is_reset_when_none_is_failed(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 2: a unit that is not failed is never reset."""
    execute()

    assert not any("reset-failed" in call for call in host.calls)


def test_a_unit_that_stays_known_fails_the_unit_step(
    execute: Callable[..., Run], host: Host, home: Path
) -> None:
    """S9h1 (#542) sentence 1: the unit check at the end of step 1 must pass."""
    host.failed_units.add(UNITS[0])
    host.failing.add("systemctl --user reset-failed")

    result = execute()

    assert result.code == 1
    assert result.lines[-1] == f"{UNIT_STEP}: {UNITS[0]} remains"
    assert (home / "presentator-live").exists()


def test_the_output_carries_no_value_from_docker_systemd_or_git(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentence 6: no container id and no commit is printed."""
    host.containers = {"secretid": "presentator-live"}
    result = execute()

    assert "secretid" not in "\n".join(result.lines)
    assert LIVE_HEAD not in "\n".join(result.lines)


def test_the_command_reads_no_env_file_and_calls_no_github(
    execute: Callable[..., Run], host: Host
) -> None:
    """S9h1 (#542) sentences 6 and 7: no .env is read, nothing is sent to GitHub."""
    execute()

    assert not any(
        ".env" in part or call[0] == "gh" for call in host.calls for part in call
    )
    assert not any(
        "github" in part and call[0] != "git" for call in host.calls for part in call
    )


def test_the_real_runner_returns_code_and_text(cutover: ModuleType) -> None:
    """S9h1 (#542) sentence 8: the runner is subprocess with an argument vector."""
    result = cutover.real_runner((sys.executable, "-c", "print('hi')"))

    assert (result.code, result.text.strip()) == (0, "hi")


def test_main_refuses_when_the_script_checkout_is_not_on_a_tag(
    cutover: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S9h1 (#542) sentences 5 and 8: a failing git is a refusal."""
    programs = tmp_path / "programs"
    programs.mkdir()
    git = programs / "git"
    git.write_text("#!/bin/sh\nexit 1\n")
    git.chmod(stat.S_IRWXU)
    monkeypatch.setenv("PATH", str(programs))
    monkeypatch.setenv("HOME", str(tmp_path))

    assert cutover.main() == REFUSED
    assert capsys.readouterr().out.startswith("refused: the checkout's HEAD")
