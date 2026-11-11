"""Under Kubernetes the worker fixes up its mount roots and judges the pod's mounts."""

import errno
import os
import socket
import stat
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest

from presentator.adapters import build_wire
from presentator.host import worker, worker_self_check
from presentator.host.worker import Boundary
from presentator.host.worker_scratch import (
    OPEN_DIRECTORY_MODE,
    QUEUE_MOUNT,
    SCRATCH_ROOTS,
    SHARED_MEMORY_MOUNT,
    TEMPORARY_MOUNT,
    WORK_MOUNT,
)
from presentator.host.worker_self_check import (
    BUILD_IDENTITY,
    CONTAINERD_SOCKETS,
    DOCKER_SOCKETS,
    HARDENED_MOUNT_OPTIONS,
    MOUNT_ROOT_MODES,
    SERVICE_ACCOUNT_DIRECTORY,
    TERMINATION_LOG,
    WIRE_IDENTITY,
    WORKER_CAPABILITIES,
    Predicate,
    RootFacts,
    Seal,
    Snapshot,
    first_failing_predicate,
    seal_from_environment,
)
from tests.presentator.host.support.audit_recording import (
    LAUNCH_EVENTS,
    READ_EVENTS,
    audit_recording,
)
from tests.presentator.host.support.sealed_snapshot import (
    POD_MOUNTS,
    READ_ONLY,
    READ_WRITE,
    SEALED_STATUS,
    SYSV_HEADERS,
    PodMount,
    read_sealed_snapshot,
    read_staged_root_as_sealed_process,
    sealed_snapshot,
    stage_kubernetes_root,
    write_pod_mountinfo,
)
from tests.presentator.host.support.worker_boundary import boundary_over

MOUNTS = Predicate.MOUNTS
SOCKET_DIRECTORY = build_wire.SOCKET_DIRECTORY
MEMORY_MOUNTS = (WORK_MOUNT, TEMPORARY_MOUNT, SHARED_MEMORY_MOUNT)
KUBERNETES_ENVIRONMENT = {worker_self_check.SEAL_VARIABLE: Seal.KUBERNETES}
BORN_TERMINATION_LOG_MODE = 0o666
CAP_MKNOD = 27
OUTSIDE_MODE = 0o700
RUNTIME_UID = sealed_snapshot().identity.runtime_uid
CHECK_OR_START = [[], ["--check"]]


@pytest.fixture
def pod(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "pod"
    root.mkdir()
    return stage_kubernetes_root(root, monkeypatch)


def judged(root: Path, seal: Seal = Seal.KUBERNETES) -> Predicate | None:
    return first_failing_predicate(read_staged_root_as_sealed_process(root, seal=seal))


def path_in(root: Path, absolute: str) -> Path:
    return root / absolute.lstrip("/")


def remount(root: Path, mount: str, **changes: str) -> None:
    write_pod_mountinfo(
        root,
        [replace(row, **changes) if row.mount == mount else row for row in POD_MOUNTS],
    )


def unmount(root: Path, mount: str) -> None:
    write_pod_mountinfo(root, [row for row in POD_MOUNTS if row.mount != mount])


def mount_extra(root: Path, row: PodMount) -> None:
    write_pod_mountinfo(root, [*POD_MOUNTS, row])


def stage_directory(root: Path, absolute: str, mode: int) -> None:
    directory = path_in(root, absolute)
    directory.mkdir(parents=True)
    directory.chmod(mode)


def link_to_host_devices(root: Path, area: str) -> str:
    (root / area).mkdir(exist_ok=True)
    (root / area / "devices").symlink_to("/dev")
    return f"/{area}/devices/null"


def with_owner(snapshot: Snapshot, mount: str, uid: int) -> Snapshot:
    kubernetes = snapshot.mounts.kubernetes
    assert kubernetes is not None

    def owned(roots: Mapping[str, RootFacts]) -> dict[str, RootFacts]:
        return {
            path: replace(facts, owner_uid=uid) if path == mount else facts
            for path, facts in roots.items()
        }

    return replace(
        snapshot,
        mounts=replace(
            snapshot.mounts,
            kubernetes=replace(
                kubernetes,
                table_roots=owned(kubernetes.table_roots),
                other_roots=owned(kubernetes.other_roots),
            ),
        ),
    )


def test_the_staged_pod_passes_the_self_check(
    pod: Path,
) -> None:
    assert judged(pod) is None


@pytest.mark.parametrize("arguments", CHECK_OR_START)
def test_a_pod_with_the_seal_unset_refuses_through_composes_mounts(
    pod: Path, caplog: pytest.LogCaptureFixture, arguments: list[str]
) -> None:
    events: list[str] = []

    status = worker.main(arguments, pod_boundary(pod, events, {}))

    assert status == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {MOUNTS}" in caplog.text
    assert "fix-up" not in events


def test_a_writable_root_refuses(pod: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def writable_root(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(f_flag=0)

    monkeypatch.setattr(worker_self_check.os, "statvfs", writable_root)

    assert judged(pod) == MOUNTS


MEMORY_MOUNT_FAULTS = {
    "not tmpfs": partial(remount, filesystem="ext4"),
    "missing": unmount,
}


@pytest.mark.parametrize("mount", MEMORY_MOUNTS)
@pytest.mark.parametrize(
    "fault", MEMORY_MOUNT_FAULTS.values(), ids=MEMORY_MOUNT_FAULTS.keys()
)
def test_a_memory_mount_that_is_not_tmpfs_or_is_missing_refuses(
    pod: Path, mount: str, fault: Callable[..., None]
) -> None:
    fault(pod, mount)

    assert judged(pod) == MOUNTS


WRONG_MODES = {
    "work not fixed up": (WORK_MOUNT, OPEN_DIRECTORY_MODE),
    "tmp without the sticky bit": (TEMPORARY_MOUNT, 0o777),
    "shared memory without the sticky bit": (SHARED_MEMORY_MOUNT, 0o777),
    "termination log as born": (TERMINATION_LOG, BORN_TERMINATION_LOG_MODE),
}


@pytest.mark.parametrize(
    ("mount", "mode"), WRONG_MODES.values(), ids=WRONG_MODES.keys()
)
def test_a_table_root_in_the_wrong_mode_refuses(
    pod: Path, mount: str, mode: int
) -> None:
    path_in(pod, mount).chmod(mode)

    assert judged(pod) == MOUNTS


@pytest.mark.parametrize("mount", MOUNT_ROOT_MODES)
def test_a_table_root_not_owned_by_root_refuses(pod: Path, mount: str) -> None:
    snapshot = read_staged_root_as_sealed_process(pod, seal=Seal.KUBERNETES)

    assert first_failing_predicate(with_owner(snapshot, mount, RUNTIME_UID)) == MOUNTS


def test_an_absent_termination_log_refuses(pod: Path) -> None:
    path_in(pod, TERMINATION_LOG).unlink()

    assert judged(pod) == MOUNTS


def test_a_claim_directory_missing_from_the_mounts_refuses(pod: Path) -> None:
    unmount(pod, SOCKET_DIRECTORY)

    assert judged(pod) == MOUNTS


@pytest.mark.parametrize("filesystem", ["ext4", "tmpfs"])
def test_the_claim_directory_may_be_a_mount_of_any_filesystem(
    pod: Path, filesystem: str
) -> None:
    remount(pod, SOCKET_DIRECTORY, filesystem=filesystem)

    assert judged(pod) is None


QUEUE_FAULTS: dict[str, Callable[[Path], None]] = {
    "missing": lambda root: unmount(root, QUEUE_MOUNT),
    "tmpfs": lambda root: remount(root, QUEUE_MOUNT, filesystem="tmpfs"),
}


@pytest.mark.parametrize("fault", QUEUE_FAULTS.values(), ids=QUEUE_FAULTS.keys())
def test_a_message_queue_directory_that_is_missing_or_tmpfs_refuses(
    pod: Path, fault: Callable[[Path], None]
) -> None:
    fault(pod)

    assert judged(pod) == MOUNTS


def test_work_and_tmp_without_the_hardening_flags_pass_under_kubernetes(
    pod: Path,
) -> None:
    flags = {
        flag
        for row in POD_MOUNTS
        if row.mount in {WORK_MOUNT, TEMPORARY_MOUNT}
        for flag in row.options.split(",")
    }

    assert not flags & HARDENED_MOUNT_OPTIONS
    assert judged(pod) is None


def test_the_same_pod_refuses_under_compose_for_the_missing_flags(pod: Path) -> None:
    remount(pod, SOCKET_DIRECTORY, filesystem="tmpfs")

    assert judged(pod, Seal.COMPOSE) == MOUNTS


def test_an_extra_read_write_directory_open_to_others_refuses(pod: Path) -> None:
    stage_directory(pod, "/data", 0o777)
    mount_extra(pod, PodMount("/data", READ_WRITE, "ext4"))

    assert judged(pod) == MOUNTS


@pytest.mark.parametrize(
    "uid",
    [BUILD_IDENTITY[0], WIRE_IDENTITY[0], RUNTIME_UID],
    ids=["build", "wire", "runtime"],
)
def test_an_extra_directory_owned_by_a_child_or_the_runtime_refuses(
    pod: Path, uid: int
) -> None:
    stage_directory(pod, "/data", 0o755)
    mount_extra(pod, PodMount("/data", READ_WRITE, "ext4"))
    snapshot = read_staged_root_as_sealed_process(pod, seal=Seal.KUBERNETES)

    assert first_failing_predicate(snapshot) is None
    assert first_failing_predicate(with_owner(snapshot, "/data", uid)) == MOUNTS


@pytest.mark.parametrize("mount", ["/etc/presentator", "/data/secret"])
def test_a_regular_file_open_to_others_refuses(pod: Path, mount: str) -> None:
    path = path_in(pod, mount)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o666)
    mount_extra(pod, PodMount(mount, READ_WRITE, "ext4"))

    assert judged(pod) == MOUNTS


def test_the_staged_extra_file_without_others_write_passes(pod: Path) -> None:
    path = path_in(pod, "/etc/extra")
    path.touch()
    path.chmod(0o644)
    mount_extra(pod, PodMount("/etc/extra", READ_ONLY, "ext4"))

    assert judged(pod) is None


@pytest.mark.parametrize("area", ["/proc/masked-directory", "/sys/masked-directory"])
def test_a_read_only_tmpfs_under_proc_or_sys_passes(pod: Path, area: str) -> None:
    stage_directory(pod, area, OPEN_DIRECTORY_MODE)
    mount_extra(pod, PodMount(area, READ_ONLY, "tmpfs"))

    assert judged(pod) is None


def test_a_read_only_tmpfs_elsewhere_refuses(pod: Path) -> None:
    stage_directory(pod, "/etc/presentator", OPEN_DIRECTORY_MODE)
    mount_extra(pod, PodMount("/etc/presentator", READ_ONLY, "tmpfs"))

    assert judged(pod) == MOUNTS


@pytest.mark.parametrize(
    ("options", "filesystem"),
    [(READ_WRITE, "tmpfs"), (READ_ONLY, "ext4")],
    ids=["read-write tmpfs", "read-only ext4"],
)
def test_only_a_read_only_tmpfs_is_masked_under_proc(
    pod: Path, options: str, filesystem: str
) -> None:
    stage_directory(pod, "/proc/unmasked", OPEN_DIRECTORY_MODE)
    mount_extra(pod, PodMount("/proc/unmasked", options, filesystem))

    assert judged(pod) == MOUNTS


def test_judging_without_the_kubernetes_read_fails_closed() -> None:
    snapshot = sealed_snapshot()
    unread = replace(
        snapshot,
        seal=Seal.KUBERNETES,
        mounts=replace(snapshot.mounts, kubernetes=None),
    )

    assert first_failing_predicate(unread) == MOUNTS


def test_a_mount_whose_root_was_not_read_refuses(pod: Path) -> None:
    stage_directory(pod, "/data", 0o755)
    mount_extra(pod, PodMount("/data", READ_WRITE, "ext4"))
    snapshot = read_staged_root_as_sealed_process(pod, seal=Seal.KUBERNETES)
    kubernetes = snapshot.mounts.kubernetes
    assert kubernetes is not None
    unread = replace(
        snapshot,
        mounts=replace(
            snapshot.mounts,
            kubernetes=replace(kubernetes, other_roots={}),
        ),
    )

    assert first_failing_predicate(unread) == MOUNTS


@pytest.mark.parametrize("area", ["proc", "sys"])
def test_a_device_bind_under_proc_or_sys_passes(pod: Path, area: str) -> None:
    mount_extra(pod, PodMount(link_to_host_devices(pod, area), READ_WRITE, "tmpfs"))

    assert judged(pod) is None


NOT_GROUP_OR_OTHERS_WRITABLE = 0o644


def make_socket(path: Path) -> None:
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(f"/proc/self/fd/{directory}/{path.name}")
    finally:
        os.close(directory)
    path.chmod(NOT_GROUP_OR_OTHERS_WRITABLE)


def make_fifo(path: Path) -> None:
    os.mkfifo(path)
    path.chmod(NOT_GROUP_OR_OTHERS_WRITABLE)


@pytest.mark.parametrize("make", [make_socket, make_fifo], ids=["socket", "fifo"])
def test_a_socket_or_fifo_root_at_an_unexpected_path_refuses(
    pod: Path, make: Callable[[Path], None]
) -> None:
    (pod / "srv").mkdir()
    make(pod / "srv/endpoint")
    mount_extra(pod, PodMount("/srv/endpoint", READ_WRITE, "tmpfs"))

    assert judged(pod) == MOUNTS


def test_a_socket_root_under_proc_refuses(pod: Path) -> None:
    make_socket(pod / "proc/endpoint")
    mount_extra(pod, PodMount("/proc/endpoint", READ_WRITE, "tmpfs"))

    assert judged(pod) == MOUNTS


def test_a_character_device_root_outside_proc_and_sys_refuses(pod: Path) -> None:
    (pod / "srv").symlink_to("/dev")
    mount_extra(pod, PodMount("/srv/null", READ_WRITE, "tmpfs"))

    assert judged(pod) == MOUNTS


def test_a_mount_point_that_cannot_be_looked_up_refuses_as_unreadable(
    pod: Path,
) -> None:
    mount_extra(pod, PodMount("/vanished", READ_WRITE, "ext4"))

    snapshot = read_staged_root_as_sealed_process(pod, seal=Seal.KUBERNETES)

    assert first_failing_predicate(snapshot) == MOUNTS
    assert snapshot.unreadable == {MOUNTS}


def make_present(path: Path) -> None:
    path.touch()


def make_dangling(path: Path) -> None:
    path.symlink_to(path.parent / "nowhere")


@pytest.mark.parametrize("make", [make_present, make_dangling], ids=["file", "link"])
@pytest.mark.parametrize("socket_path", [*DOCKER_SOCKETS, *CONTAINERD_SOCKETS])
def test_a_container_runtime_socket_refuses(
    pod: Path, socket_path: str, make: Callable[[Path], None]
) -> None:
    path_in(pod, socket_path).parent.mkdir(parents=True, exist_ok=True)
    make(path_in(pod, socket_path))

    assert judged(pod) == MOUNTS


def test_a_service_account_directory_refuses(pod: Path) -> None:
    path_in(pod, SERVICE_ACCOUNT_DIRECTORY).mkdir(parents=True)

    assert judged(pod) == MOUNTS


def test_compose_reads_and_judges_nothing_the_pod_adds(
    sealed_root: Path,
) -> None:
    path_in(sealed_root, CONTAINERD_SOCKETS[0]).parent.mkdir(parents=True)
    path_in(sealed_root, CONTAINERD_SOCKETS[0]).touch()
    stage_directory(sealed_root, "/data", 0o777)
    unreachable = PodMount("/vanished", READ_WRITE, "ext4")
    open_directory = PodMount("/data", READ_WRITE, "ext4")
    mountinfo = path_in(sealed_root, "/proc/self/mountinfo")
    mountinfo.write_text(
        mountinfo.read_text()
        + "".join(
            f"90 20 0:90 / {row.mount} {row.options} - ext4 ext4 rw\n"
            for row in (unreachable, open_directory)
        )
    )

    snapshot = read_staged_root_as_sealed_process(sealed_root)

    assert first_failing_predicate(snapshot) is None
    assert snapshot.unreadable == frozenset()
    assert snapshot.mounts.kubernetes is None


def pod_boundary(
    root: Path,
    events: list[str],
    environment: Mapping[str, str],
    fix_up: Callable[[], None] | None = None,
) -> Boundary:
    def lower() -> None:
        events.append("lower")

    def fix_up_the_pod() -> None:
        events.append("fix-up")
        (fix_up or partial(worker.fix_up_mounts, root))()

    def read(seal: Seal) -> Snapshot:
        events.append("read")
        return read_staged_root_as_sealed_process(root, seal=seal)

    return replace(
        boundary_over(sealed_snapshot(), events),
        read_seal=partial(seal_from_environment, environment),
        lower_process_limit=lower,
        fix_up_mounts=fix_up_the_pod,
        read_snapshot=read,
    )


def born_pod(root: Path) -> None:
    path_in(root, WORK_MOUNT).chmod(OPEN_DIRECTORY_MODE)
    path_in(root, TERMINATION_LOG).chmod(BORN_TERMINATION_LOG_MODE)


@pytest.mark.parametrize("arguments", CHECK_OR_START)
def test_kubernetes_fixes_up_the_born_pod_between_the_lowering_and_the_read(
    pod: Path, caplog: pytest.LogCaptureFixture, arguments: list[str]
) -> None:
    born_pod(pod)
    events: list[str] = []

    status = worker.main(arguments, pod_boundary(pod, events, KUBERNETES_ENVIRONMENT))

    assert status == 0
    assert events[:5] == ["prctl", "umask", "lower", "fix-up", "read"]
    assert ("serve" in events) is (not arguments)
    assert "start refused" not in caplog.text
    assert (
        stat.S_IMODE(path_in(pod, WORK_MOUNT).stat().st_mode)
        == (MOUNT_ROOT_MODES[WORK_MOUNT])
    )
    assert (
        stat.S_IMODE(path_in(pod, TERMINATION_LOG).stat().st_mode)
        == (MOUNT_ROOT_MODES[TERMINATION_LOG])
    )


def remove_termination_log(root: Path) -> None:
    path_in(root, TERMINATION_LOG).unlink()


def deny(_root: Path) -> Callable[[], None]:
    def denied() -> None:
        raise PermissionError(errno.EPERM, "denied")

    return denied


FIX_UP_FAILURES = {
    "a missing path": remove_termination_log,
    "a denied change": deny,
}


@pytest.mark.parametrize(
    "failure", FIX_UP_FAILURES.values(), ids=FIX_UP_FAILURES.keys()
)
@pytest.mark.parametrize("arguments", CHECK_OR_START)
def test_a_failed_fix_up_refuses_the_start_as_mounts_before_the_read(
    pod: Path,
    caplog: pytest.LogCaptureFixture,
    arguments: list[str],
    failure: Callable[[Path], Callable[[], None] | None],
) -> None:
    events: list[str] = []
    fix_up = failure(pod)

    status = worker.main(
        arguments, pod_boundary(pod, events, KUBERNETES_ENVIRONMENT, fix_up)
    )

    assert status == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {MOUNTS}" in caplog.text
    assert events == ["prctl", "umask", "lower", "fix-up"]


COMPOSE_ENVIRONMENTS = {
    "compose": {worker_self_check.SEAL_VARIABLE: Seal.COMPOSE},
    "unset": {},
}


@pytest.mark.parametrize(
    "environment", COMPOSE_ENVIRONMENTS.values(), ids=COMPOSE_ENVIRONMENTS.keys()
)
@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], ["prctl", "umask", "read", "settings", "serve"]),
        (["--check"], ["prctl", "umask", "read"]),
    ],
)
def test_compose_and_an_unset_seal_run_no_fix_up(
    arguments: list[str], expected: list[str], environment: Mapping[str, str]
) -> None:
    events: list[str] = []
    boundary = replace(
        boundary_over(sealed_snapshot(), events),
        read_seal=partial(seal_from_environment, environment),
        fix_up_mounts=lambda: events.append("fix-up"),
    )

    status = worker.main(arguments, boundary)

    assert (status, events) == (0, expected)


def break_mountinfo_line(root: Path) -> None:
    path_in(root, "/proc/self/mountinfo").write_text("a line of too few fields\n")


def break_mountinfo_encoding(root: Path) -> None:
    path_in(root, "/proc/self/mountinfo").write_bytes(b"\xff\xfe\n")


@pytest.mark.parametrize("arguments", CHECK_OR_START)
@pytest.mark.parametrize("break_it", [break_mountinfo_line, break_mountinfo_encoding])
def test_a_malformed_mountinfo_refuses_the_pod_as_mounts_without_a_traceback(
    pod: Path,
    caplog: pytest.LogCaptureFixture,
    arguments: list[str],
    break_it: Callable[[Path], None],
) -> None:
    break_it(pod)

    status = worker.main(arguments, pod_boundary(pod, [], KUBERNETES_ENVIRONMENT))

    assert status == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {MOUNTS}" in caplog.text
    assert "Traceback" not in caplog.text


def test_a_symlinked_table_root_refuses_and_its_target_keeps_its_mode(
    pod: Path,
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    outside.chmod(OUTSIDE_MODE)
    temporary = path_in(pod, TEMPORARY_MOUNT)
    temporary.rmdir()
    temporary.symlink_to(outside)
    events: list[str] = []

    status = worker.main([], pod_boundary(pod, events, KUBERNETES_ENVIRONMENT))

    assert status == worker.REFUSAL_EXIT_STATUS
    assert f"start refused: {MOUNTS}" in caplog.text
    assert events == ["prctl", "umask", "lower", "fix-up"]
    assert stat.S_IMODE(outside.stat().st_mode) == OUTSIDE_MODE


def test_under_kubernetes_the_fix_up_opens_the_table_paths_then_the_read_starts(
    pod: Path,
) -> None:
    audit_recording.start()
    try:
        worker.fix_up_mounts(pod)
        read_sealed_snapshot(pod, seal=Seal.KUBERNETES)
    finally:
        audit_recording.stop()
    events = audit_recording.events
    reads = [
        (event, Path(str(path)).relative_to(pod))
        for event, path in events
        if event in READ_EVENTS and Path(str(path)).is_relative_to(pod)
    ]

    table_paths = [Path(path.lstrip("/")) for path in MOUNT_ROOT_MODES]
    fix_up_reads = len(table_paths)
    scratch_reads = len(SYSV_HEADERS) + len(SCRATCH_ROOTS)
    assert not [event for event, _ in events if event in LAUNCH_EVENTS]
    assert sorted(reads[:fix_up_reads]) == sorted(("open", p) for p in table_paths)
    assert reads[fix_up_reads] == ("open", Path("sys/fs/cgroup/pids.current"))
    assert sorted(reads[fix_up_reads + 1 : fix_up_reads + 1 + scratch_reads]) == sorted(
        [("os.scandir", Path(root.lstrip("/"))) for root in SCRATCH_ROOTS]
        + [("open", Path("proc/sysvipc") / table) for table in SYSV_HEADERS]
    )
    later_scans = [
        path
        for event, path in reads[fix_up_reads + 1 + scratch_reads :]
        if event in {"os.scandir", "os.listdir"}
    ]
    assert all(path.parts[0] == "opt" for path in later_scans)


def test_without_no_new_privileges_the_pod_refuses_as_capabilities(pod: Path) -> None:
    path_in(pod, "/proc/self/status").write_text(
        SEALED_STATUS.replace("NoNewPrivs:\t1", "NoNewPrivs:\t0")
    )

    assert judged(pod) == Predicate.CAPABILITIES


def test_a_bounding_set_holding_the_node_creation_capability_refuses(pod: Path) -> None:
    widened = f"{WORKER_CAPABILITIES | 1 << CAP_MKNOD:016x}"
    path_in(pod, "/proc/self/status").write_text(
        SEALED_STATUS.replace("CapBnd:\t00000000000000e0", f"CapBnd:\t{widened}")
    )

    assert judged(pod) == Predicate.CAPABILITIES
