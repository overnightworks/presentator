"""The build worker refuses to start unless the container is sealed as designed."""

import os
import stat
import sys
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.adapters import build_wire
from presentator.host import worker
from presentator.host.config import (
    WorkerSettings,
    load_worker_settings,
    worker_settings_variables,
)
from presentator.host.worker import (
    Boundary,
    Predicate,
    Snapshot,
)
from presentator.host.worker_scratch import QUEUE_MOUNT, scratch_empty
from presentator.host.worker_serving import Serving
from tests.presentator.host.support.sealed_snapshot import (
    HARDENED,
    LOOPBACK_ONLY,
    SEALED_STATUS,
    SYSV_HEADERS,
    sealed_snapshot,
    stage_sealed_root,
    with_fields,
)

WORK_MOUNT, TEMPORARY_MOUNT, SHARED_MEMORY_MOUNT, *_ = worker.TMPFS_MOUNTS


def with_status(**changes: str) -> Callable[[Snapshot], Snapshot]:
    def change(snapshot: Snapshot) -> Snapshot:
        lines = snapshot.process_status.splitlines()
        kept = [line for line in lines if line.partition(":")[0] not in changes]
        edited = [*kept, *(f"{name}:\t{value}" for name, value in changes.items())]
        return replace(snapshot, process_status="\n".join(edited))

    return change


def with_identity(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, identity=replace(snapshot.identity, **changes)
    )


def with_users(**users: tuple[int, int]) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: with_identity(users={**snapshot.identity.users, **users})(
        snapshot
    )


def with_toolchain(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, toolchain=replace(snapshot.toolchain, **changes)
    )


def with_mountinfo(old: str, new: str) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, mountinfo=snapshot.mountinfo.replace(old, new)
    )


def with_occupied_roots(*roots: str) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, scratch=replace(snapshot.scratch, occupied_roots=frozenset(roots))
    )


GROUP_WRITABLE = stat.S_IFREG | 0o775
FAILURES = {
    "dumpable": (with_fields(dumpable=1), Predicate.NON_DUMPABLE),
    "CAP_SYS_ADMIN effective": (
        with_status(CapEff="00000000002000e0"),
        Predicate.CAPABILITIES,
    ),
    "permitted capability missing": (
        with_status(CapPrm="0000000000000060"),
        Predicate.CAPABILITIES,
    ),
    "inheritable capability": (
        with_status(CapInh="0000000000000020"),
        Predicate.CAPABILITIES,
    ),
    "ambient capability": (
        with_status(CapAmb="0000000000000020"),
        Predicate.CAPABILITIES,
    ),
    "wider bounding set": (
        with_status(CapBnd="00000000000000ff"),
        Predicate.CAPABILITIES,
    ),
    "new privileges allowed": (with_status(NoNewPrivs="0"), Predicate.CAPABILITIES),
    "no seccomp filter": (with_status(Seccomp="0"), Predicate.CAPABILITIES),
    "writable root": (with_fields(root_read_only=False), Predicate.MOUNTS),
    "work not tmpfs": (
        with_mountinfo(
            f"{WORK_MOUNT} {HARDENED} - tmpfs", f"{WORK_MOUNT} {HARDENED} - ext4"
        ),
        Predicate.MOUNTS,
    ),
    "tmp without nosuid": (
        with_mountinfo(f"{TEMPORARY_MOUNT} rw,nosuid,", f"{TEMPORARY_MOUNT} rw,"),
        Predicate.MOUNTS,
    ),
    "work without noexec": (
        with_mountinfo(f"{WORK_MOUNT} {HARDENED}", f"{WORK_MOUNT} rw,nosuid,nodev"),
        Predicate.MOUNTS,
    ),
    "dev shm missing": (
        with_mountinfo(SHARED_MEMORY_MOUNT, "/dev/other"),
        Predicate.MOUNTS,
    ),
    "work sticky world-writable": (with_fields(work_mode=0o1777), Predicate.MOUNTS),
    "work not root": (with_fields(work_owner_uid=1000), Predicate.MOUNTS),
    "second interface": (
        with_fields(network_devices=LOOPBACK_ONLY + "  eth0: 0 0\n"),
        Predicate.MOUNTS,
    ),
    "docker socket present": (
        with_fields(docker_socket_present=True),
        Predicate.MOUNTS,
    ),
    "pids unlimited": (with_fields(pids_max="max"), Predicate.RESOURCE_BOUNDS),
    "pids above bound": (with_fields(pids_max="513"), Predicate.RESOURCE_BOUNDS),
    "memory unlimited": (with_fields(memory_max="max"), Predicate.RESOURCE_BOUNDS),
    "build uid root": (
        with_users(**{"presentator-build": (0, 0)}),
        Predicate.IDENTITIES,
    ),
    "build uid is runtime uid": (
        with_identity(runtime_uid=10001),
        Predicate.IDENTITIES,
    ),
    "wire uid root": (with_users(**{"presentator-wire": (0, 0)}), Predicate.IDENTITIES),
    "wire uid is runtime uid": (with_identity(runtime_uid=10002), Predicate.IDENTITIES),
    "wire uid is build uid": (
        with_users(**{"presentator-wire": (10001, 10001)}),
        Predicate.IDENTITIES,
    ),
    "wrong controller gid": (with_identity(gid=0), Predicate.IDENTITIES),
    "extra supplementary group": (
        with_identity(supplementary_gids=(1000, 4)),
        Predicate.IDENTITIES,
    ),
    "not root": (with_identity(uid=1000, euid=1000), Predicate.IDENTITIES),
    "secret key in environment": (
        with_fields(environment_keys=frozenset({"PATH", "PRESENTATOR_SECRET_KEY"})),
        Predicate.ENVIRONMENT,
    ),
    "not pid one": (with_fields(pid=7), Predicate.PID_ONE),
    "another task in the container": (with_fields(cgroup_tasks="2"), Predicate.ALONE),
    "entry in work": (with_occupied_roots(WORK_MOUNT), Predicate.SCRATCH),
    "entry in the message queue directory": (
        with_occupied_roots(QUEUE_MOUNT),
        Predicate.SCRATCH,
    ),
    "message queue directory missing": (
        with_mountinfo(QUEUE_MOUNT, "/dev/other"),
        Predicate.MOUNTS,
    ),
    "message queue directory not an mqueue": (
        with_mountinfo(" - mqueue mqueue", " - tmpfs mqueue"),
        Predicate.MOUNTS,
    ),
    "slidev missing": (with_toolchain(slidev_mode=None), Predicate.TOOLCHAIN),
    "corepack missing": (with_toolchain(corepack_mode=None), Predicate.TOOLCHAIN),
    "headless shell missing": (
        with_toolchain(headless_shell_modes=()),
        Predicate.TOOLCHAIN,
    ),
    "headless shell matched twice": (
        with_toolchain(headless_shell_modes=(GROUP_WRITABLE & ~0o020,) * 2),
        Predicate.TOOLCHAIN,
    ),
    "headless shell group-writable": (
        with_toolchain(headless_shell_modes=(GROUP_WRITABLE,)),
        Predicate.TOOLCHAIN,
    ),
    "toolchain not executable by others": (
        with_toolchain(slidev_mode=stat.S_IFREG | 0o750),
        Predicate.TOOLCHAIN,
    ),
    "vite cache a directory": (
        with_toolchain(vite_link_target=None),
        Predicate.TOOLCHAIN,
    ),
    "vite cache linked elsewhere": (
        with_toolchain(vite_link_target="/elsewhere/vite"),
        Predicate.TOOLCHAIN,
    ),
}


LOADED_SETTINGS = WorkerSettings(build_timeout_seconds=45.0, build_output_megabytes=7)


def boundary_over(
    snapshot: Snapshot,
    events: list[str],
    served: dict[str, object] | None = None,
    load_settings: Callable[[], WorkerSettings] = lambda: LOADED_SETTINGS,
) -> Boundary:
    def read_snapshot() -> Snapshot:
        events.append("read")
        return snapshot

    def settings() -> WorkerSettings:
        events.append("settings")
        return load_settings()

    def serve(**arguments: object) -> int:
        events.append("serve")
        if served is not None:
            served.update(arguments)
        return 0

    return Boundary(
        set_non_dumpable=lambda: events.append("prctl"),
        set_umask=lambda: events.append("umask"),
        read_snapshot=read_snapshot,
        load_settings=settings,
        serve=serve,
    )


def test_sealed_container_passes_the_self_check() -> None:
    assert worker.first_failing_predicate(sealed_snapshot()) is None


# DK4
@pytest.mark.parametrize(("change", "refused"), FAILURES.values(), ids=FAILURES.keys())
def test_self_check_names_the_failing_predicate(
    change: Callable[[Snapshot], Snapshot], refused: Predicate
) -> None:
    assert worker.first_failing_predicate(change(sealed_snapshot())) == refused


def test_double_fault_names_the_first_predicate_in_refusal_order() -> None:
    snapshot = with_identity(gid=0)(with_status(Seccomp="0")(sealed_snapshot()))

    assert worker.first_failing_predicate(snapshot) == Predicate.CAPABILITIES


def test_sealed_check_exits_zero_without_serving() -> None:
    events: list[str] = []

    assert worker.main(["--check"], boundary_over(sealed_snapshot(), events)) == 0
    assert "serve" not in events


def test_sealed_start_serves_and_returns_the_serving_status() -> None:
    events: list[str] = []

    assert worker.main([], boundary_over(sealed_snapshot(), events)) == 0
    assert events[-1] == "serve"


def test_start_serves_for_the_runtime_uid_and_the_controller_group() -> None:
    served: dict[str, object] = {}
    snapshot = with_identity(runtime_uid=1001)(sealed_snapshot())

    worker.main([], boundary_over(snapshot, [], served))

    assert served == {
        "controller_gid": 1000,
        "runtime_uid": 1001,
        "settings": LOADED_SETTINGS,
    }


def test_refusal_exits_two_and_logs_the_predicate_without_environment_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    snapshot = with_fields(
        pid=7, environment_keys=frozenset({"PRESENTATOR_SECRET_KEY"})
    )(sealed_snapshot())

    assert (
        worker.main(["--check"], boundary_over(snapshot, []))
        == worker.REFUSAL_EXIT_STATUS
    )
    assert Predicate.ENVIRONMENT in caplog.text
    assert "PRESENTATOR_SECRET_KEY" not in caplog.text


def test_start_up_makes_the_process_non_dumpable_before_reading_anything() -> None:
    events: list[str] = []

    worker.main(["--check"], boundary_over(sealed_snapshot(), events))

    assert events == ["prctl", "umask", "read"]


def test_a_start_loads_the_settings_after_the_self_check_and_before_serving() -> None:
    events: list[str] = []

    worker.main([], boundary_over(sealed_snapshot(), events))

    assert events == ["prctl", "umask", "read", "settings", "serve"]


def test_a_refused_self_check_never_loads_the_settings() -> None:
    events: list[str] = []
    snapshot = with_fields(pid=7)(sealed_snapshot())

    assert (
        worker.main([], boundary_over(snapshot, events)) == worker.REFUSAL_EXIT_STATUS
    )
    assert "settings" not in events


@pytest.mark.parametrize(
    "occupied", [with_fields(cgroup_tasks="2"), with_occupied_roots(WORK_MOUNT)]
)
def test_a_start_refused_for_alone_or_scratch_never_loads_settings_or_serves(
    occupied: Callable[[Snapshot], Snapshot],
) -> None:
    events: list[str] = []

    status = worker.main([], boundary_over(occupied(sealed_snapshot()), events))

    assert (status, events) == (worker.REFUSAL_EXIT_STATUS, ["prctl", "umask", "read"])


@pytest.mark.parametrize(
    "variable",
    ["PRESENTATOR_BUILD_TIMEOUT_SECONDS", "PRESENTATOR_BUILD_OUTPUT_MEGABYTES"],
)
def test_an_invalid_setting_refuses_by_variable_name_never_the_value(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    events: list[str] = []
    monkeypatch.setenv(variable, "unique-bad-value-4711")

    status = worker.main(
        [],
        boundary_over(
            sealed_snapshot(), events, None, load_settings=load_worker_settings
        ),
    )

    assert status == worker.REFUSAL_EXIT_STATUS
    assert events[-1] == "settings"
    assert variable in caplog.text
    assert worker.StartRefusal.SETTINGS in caplog.text
    assert "unique-bad-value-4711" not in caplog.text


def test_the_settings_keys_pass_the_environment_predicate() -> None:
    snapshot = with_fields(environment_keys=worker_settings_variables())(
        sealed_snapshot()
    )

    assert worker.first_failing_predicate(snapshot) is None


def test_the_real_boundary_serves_the_socket_directory_bounded_by_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served: dict[str, object] = {}

    def recording_serve(directory: Path, **arguments: object) -> int:
        served.update(arguments, directory=directory)
        return 0

    monkeypatch.setattr(worker, "serve", recording_serve)

    status = worker.REAL_BOUNDARY.serve(
        controller_gid=1000, runtime_uid=1001, settings=LOADED_SETTINGS
    )

    assert status == 0
    assert served["directory"] == Path(build_wire.SOCKET_DIRECTORY)
    assert (
        served["controller_uid"],
        served["controller_gid"],
        served["runtime_uid"],
    ) == (0, 1000, 1001)
    assert isinstance(served["serving"], Serving)
    assert (
        served["serving"].build_timeout_seconds,
        served["serving"].build_output_megabytes,
    ) == (45.0, 7)


def test_failing_prctl_refuses_before_reading_anything() -> None:
    events: list[str] = []

    def failing_prctl() -> None:
        raise OSError

    boundary = replace(
        boundary_over(sealed_snapshot(), events), set_non_dumpable=failing_prctl
    )

    assert worker.main(["--check"], boundary) == worker.REFUSAL_EXIT_STATUS
    assert events == []


@pytest.fixture
def sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return stage_sealed_root(tmp_path, monkeypatch)


def test_reads_the_sealed_facts_from_the_filesystem(sealed_root: Path) -> None:
    snapshot = worker.read_snapshot(sealed_root)

    assert snapshot.process_status == SEALED_STATUS
    assert snapshot.network_devices == LOOPBACK_ONLY
    assert (snapshot.pids_max, snapshot.memory_max) == ("512", "1073741824")
    assert (snapshot.work_mode, snapshot.work_owner_uid) == (
        0o755,
        sealed_root.stat().st_uid,
    )
    assert not snapshot.docker_socket_present
    assert snapshot.identity.users["presentator-wire"] == (10002, 10002)
    assert snapshot.toolchain.vite_link_target == "/work/vite"
    assert len(snapshot.toolchain.headless_shell_modes) == 1
    assert snapshot.toolchain.corepack_mode is not None
    assert snapshot.cgroup_tasks == "1"
    assert scratch_empty(snapshot.scratch)
    assert snapshot.unreadable == frozenset()


def test_reads_a_missing_toolchain_as_absent(sealed_root: Path) -> None:
    (sealed_root / "app/slidev/node_modules/.bin/slidev").unlink()

    toolchain = worker.read_snapshot(sealed_root).toolchain

    assert toolchain.slidev_mode is None


def break_capability_bounding_set(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    status = SEALED_STATUS.replace("CapBnd:\t00000000000000e0", "CapBnd:\tzz")
    (root / "proc/self/status").write_text(status)


def remove_status(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "proc/self/status").unlink()


def remove_network_table(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "proc/net/dev").unlink()


def break_mountinfo(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "proc/self/mountinfo").write_text("garbage\n")


def break_mountinfo_encoding(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "proc/self/mountinfo").write_bytes(b"\xff\n")


def loop_slidev_package(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    link = root / "app/slidev/node_modules/@slidev/cli"
    link.unlink()
    link.symlink_to(link)


def deepen_slidev_package_chain(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    link = root / "app/slidev/node_modules/@slidev/cli"
    chain_length = sys.getrecursionlimit() + 100
    package = link.resolve()
    link.unlink()
    previous = package
    for hop in range(chain_length):
        hop_link = link.parent / f"hop-{hop}"
        hop_link.symlink_to(previous)
        previous = hop_link
    link.symlink_to(previous)


def break_slidev_package_link(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    link = root / "app/slidev/node_modules/@slidev/cli"
    package = link.resolve()
    link.unlink()
    link.symlink_to(root / "missing" / ".." / package.relative_to(root))


def grant_extra_capability(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    status = SEALED_STATUS.replace(
        "CapEff:\t00000000000000e0", "CapEff:\t00000000002000e0"
    )
    (root / "proc/self/status").write_text(status)


def grant_capability_and_break_mountinfo_encoding(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_extra_capability(root, monkeypatch)
    break_mountinfo_encoding(root, monkeypatch)


def grant_capability_and_loop_slidev_package(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_extra_capability(root, monkeypatch)
    loop_slidev_package(root, monkeypatch)


def grant_capability_and_deepen_slidev_package_chain(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_extra_capability(root, monkeypatch)
    deepen_slidev_package_chain(root, monkeypatch)


def grant_capability_and_break_slidev_package_link(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grant_extra_capability(root, monkeypatch)
    break_slidev_package_link(root, monkeypatch)


def remove_work(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "work").rmdir()


def fail_dumpable_read(_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(_option: int, _value: int) -> int:
        raise OSError

    monkeypatch.setattr(worker, "libc_prctl", failing)


def set_task_count(count: str) -> Callable[[Path, pytest.MonkeyPatch], None]:
    def arrange(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
        (root / "sys/fs/cgroup/pids.current").write_text(count)

    return arrange


def remove_task_count(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "sys/fs/cgroup/pids.current").unlink()


def remove_temporary_directory(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "tmp").rmdir()


def fill_work(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    (root / "work/deck").mkdir()


def replace_table(
    table: str, content: str | None
) -> Callable[[Path, pytest.MonkeyPatch], None]:
    def arrange(root: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
        path = root / "proc/sysvipc" / table
        path.unlink() if content is None else path.write_text(content)

    return arrange


REFUSED_FACTS = {
    "capability bounding set not hexadecimal": (
        break_capability_bounding_set,
        Predicate.CAPABILITIES,
    ),
    "status file missing": (remove_status, Predicate.CAPABILITIES),
    "mountinfo malformed": (break_mountinfo, Predicate.MOUNTS),
    "work directory missing": (remove_work, Predicate.MOUNTS),
    "network table missing": (remove_network_table, Predicate.MOUNTS),
    "mountinfo not UTF-8": (break_mountinfo_encoding, Predicate.MOUNTS),
    "extra capability before non-UTF-8 mountinfo": (
        grant_capability_and_break_mountinfo_encoding,
        Predicate.CAPABILITIES,
    ),
    "extra capability before slidev package symlink loop": (
        grant_capability_and_loop_slidev_package,
        Predicate.CAPABILITIES,
    ),
    "extra capability before an over-deep slidev package chain": (
        grant_capability_and_deepen_slidev_package_chain,
        Predicate.CAPABILITIES,
    ),
    "extra capability before a broken slidev package link": (
        grant_capability_and_break_slidev_package_link,
        Predicate.CAPABILITIES,
    ),
    "dumpable unreadable": (fail_dumpable_read, Predicate.NON_DUMPABLE),
    "task count missing": (remove_task_count, Predicate.ALONE),
    "task count empty": (set_task_count(""), Predicate.ALONE),
    "task count not decimal": (set_task_count("one\n"), Predicate.ALONE),
    "task count two": (set_task_count("2\n"), Predicate.ALONE),
    "entry in work": (fill_work, Predicate.SCRATCH),
    "temporary directory missing": (remove_temporary_directory, Predicate.SCRATCH),
    "shared memory table missing": (replace_table("shm", None), Predicate.SCRATCH),
    "message table empty": (replace_table("msg", ""), Predicate.SCRATCH),
    "semaphore table foreign": (replace_table("sem", "foreign\n"), Predicate.SCRATCH),
}


@pytest.mark.parametrize(
    ("arrange", "refused"), REFUSED_FACTS.values(), ids=REFUSED_FACTS.keys()
)
def test_unreadable_or_unparsable_fact_refuses_through_main_naming_its_predicate(
    sealed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    arrange: Callable[[Path, pytest.MonkeyPatch], None],
    refused: Predicate,
) -> None:
    arrange(sealed_root, monkeypatch)
    sealed = sealed_snapshot()

    def read_with_sealed_process_facts() -> Snapshot:
        return replace(
            worker.read_snapshot(sealed_root),
            environment_keys=sealed.environment_keys,
            pid=sealed.pid,
            work_owner_uid=sealed.work_owner_uid,
            identity=sealed.identity,
        )

    boundary = replace(
        boundary_over(sealed, []), read_snapshot=read_with_sealed_process_facts
    )

    assert worker.main(["--check"], boundary) == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {refused}" in caplog.text


@pytest.fixture(params=["/run/docker.sock", "/var/run/docker.sock"])
def socket_path(request: pytest.FixtureRequest, sealed_root: Path) -> Path:
    path = sealed_root / request.param.lstrip("/")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def make_socket_dangling(path: Path) -> None:
    path.symlink_to(path.parent / "nowhere")


def make_socket_present(path: Path) -> None:
    path.touch()


@pytest.mark.parametrize(
    "make_socket",
    [make_socket_present, make_socket_dangling],
    ids=["present", "dangling symlink"],
)
def test_reads_a_docker_socket_as_present(
    sealed_root: Path, socket_path: Path, make_socket: Callable[[Path], None]
) -> None:
    make_socket(socket_path)

    assert worker.read_snapshot(sealed_root).docker_socket_present is True


def test_denied_socket_lookup_marks_mounts_unreadable(
    sealed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path: Path) -> os.stat_result:
        raise PermissionError(path)

    monkeypatch.setattr(worker.os, "lstat", denied)

    assert worker.read_snapshot(sealed_root).unreadable == {Predicate.MOUNTS}


@pytest.mark.parametrize(
    "arrange",
    [loop_slidev_package, break_slidev_package_link, deepen_slidev_package_chain],
    ids=["loop", "broken link", "over-deep chain"],
)
def test_unresolvable_slidev_package_gives_no_vite_target(
    sealed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    arrange: Callable[[Path, pytest.MonkeyPatch], None],
) -> None:
    arrange(sealed_root, monkeypatch)

    assert worker.read_snapshot(sealed_root).toolchain.vite_link_target is None


def unknown_user(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(name: str) -> None:
        raise KeyError(name)

    monkeypatch.setattr(worker.pwd, "getpwnam", refuse)


def test_unreadable_resource_bound_marks_its_predicate_unreadable(
    sealed_root: Path,
) -> None:
    (sealed_root / "sys/fs/cgroup/memory.max").unlink()

    unreadable = worker.read_snapshot(sealed_root).unreadable

    assert unreadable == {Predicate.RESOURCE_BOUNDS}


def test_unknown_user_marks_identities_unreadable(
    sealed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unknown_user(monkeypatch)

    assert worker.read_snapshot(sealed_root).unreadable == {Predicate.IDENTITIES}


def test_unreadable_later_fact_does_not_mask_an_earlier_failing_predicate(
    sealed_root: Path,
) -> None:
    (sealed_root / "proc/self/status").write_text(
        SEALED_STATUS.replace("00000000000000e0", "00000000002000e0", 1)
    )
    (sealed_root / "work").rmdir()

    snapshot = worker.read_snapshot(sealed_root)

    assert worker.first_failing_predicate(snapshot) == Predicate.CAPABILITIES
    assert snapshot.unreadable == {Predicate.MOUNTS, Predicate.SCRATCH}


def test_real_process_effects_make_it_non_dumpable_and_set_the_umask() -> None:
    previous_umask = os.umask(0)
    original_dumpable = worker.libc_prctl(worker.PR_GET_DUMPABLE, 0)
    try:
        worker.REAL_BOUNDARY.set_non_dumpable()
        worker.REAL_BOUNDARY.set_umask()
        assert worker.libc_prctl(worker.PR_GET_DUMPABLE, 0) == 0
        assert os.umask(previous_umask) == worker.ONLY_THE_OWNER
    finally:
        os.umask(previous_umask)
        worker.libc_prctl(worker.PR_SET_DUMPABLE, original_dumpable)


def test_prctl_failure_raises() -> None:
    with pytest.raises(OSError, match="prctl"):
        worker.libc_prctl(0x7FFFFFFF, 0)


LAUNCH_EVENTS = frozenset(
    {
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "os.system",
        "subprocess.Popen",
    }
)
READ_EVENTS = frozenset({"open", "os.scandir", "os.listdir"})
audit_recording: dict[str, object] = {"on": False, "thread": 0, "events": []}


def record_audited_event(event: str, arguments: tuple[object, ...]) -> None:
    if not audit_recording["on"] or audit_recording["thread"] != threading.get_ident():
        return
    if event in LAUNCH_EVENTS or (event in READ_EVENTS and arguments):
        audit_recording["events"].append((event, arguments[0] if arguments else None))  # type: ignore[attr-defined]


def test_the_snapshot_reads_the_task_count_then_the_scratch_places_and_launches_nothing(
    sealed_root: Path,
) -> None:
    sys.addaudithook(record_audited_event)
    audit_recording.update(on=True, thread=threading.get_ident(), events=[])
    try:
        worker.read_snapshot(sealed_root)
    finally:
        audit_recording["on"] = False
    events: list[tuple[str, object]] = audit_recording["events"]  # type: ignore[assignment]

    def below_root(event: tuple[str, object]) -> bool:
        return event[0] in READ_EVENTS and Path(str(event[1])).is_relative_to(
            sealed_root
        )

    reads = [
        (event, Path(str(path)).relative_to(sealed_root))
        for event, path in events
        if below_root((event, path))
    ]
    assert not [event for event, _ in events if event in LAUNCH_EVENTS]
    assert reads[0] == ("open", Path("sys/fs/cgroup/pids.current"))
    assert sorted(reads[1:8]) == sorted(
        [
            ("os.scandir", Path(root))
            for root in ("work", "tmp", "dev/shm", "dev/mqueue")
        ]
        + [("open", Path("proc/sysvipc") / table) for table in SYSV_HEADERS]
    )
