"""The sealed snapshot the self-check passes, its staged root and its alterations."""

import ipaddress
import os
import socket
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from presentator.adapters import rtnetlink
from presentator.host import worker_self_check
from presentator.host.worker_scratch import QUEUE_MOUNT, Scratch
from presentator.host.worker_self_check import (
    MAXIMUM_PROCESSES,
    Identity,
    Mounts,
    ResourceBounds,
    Seal,
    Snapshot,
    Toolchain,
    read_snapshot,
)

SEALED_STATUS = """Name:\tpresentator-worker
CapInh:\t0000000000000000
CapPrm:\t00000000000000e0
CapEff:\t00000000000000e0
CapBnd:\t00000000000000e0
CapAmb:\t0000000000000000
NoNewPrivs:\t1
Seccomp:\t2
"""
HARDENED = "rw,nosuid,nodev,noexec"
MESSAGE_QUEUE_MOUNTINFO = f"40 20 0:40 / {QUEUE_MOUNT} {HARDENED} - mqueue mqueue rw\n"
SEALED_MOUNTINFO = (
    "20 1 0:20 / / ro,relatime - overlay overlay rw\n"
    + "".join(
        f"{21 + index} 20 0:{21 + index} / {mount} {HARDENED} - tmpfs tmpfs rw\n"
        for index, mount in enumerate(worker_self_check.TMPFS_MOUNTS)
    )
    + MESSAGE_QUEUE_MOUNTINFO
)
# The kernel's own header lines of its empty SysV tables, as read in a container.
SYSV_HEADERS = {
    "shm": (
        "       key      shmid perms                  size  cpid  lpid nattch"
        "   uid   gid  cuid  cgid      atime      dtime      ctime"
        "                   rss                  swap\n"
    ),
    "msg": (
        "       key      msqid perms      cbytes       qnum lspid lrpid"
        "   uid   gid  cuid  cgid      stime      rtime      ctime\n"
    ),
    "sem": (
        "       key      semid perms      nsems   uid   gid  cuid  cgid"
        "      otime      ctime\n"
    ),
}
WORLD_READABLE_EXECUTABLE = 0o755
LOOPBACK_INDEX = 1
LINK_UP = 0x1


def limits_file(soft: str, hard: str) -> str:
    """The kernel's `proc/self/limits` layout, with the given `Max processes` row."""

    def row(name: str, soft: str, hard: str, units: str = "") -> str:
        return f"{name:<25} {soft:<20} {hard:<20} {units:<10}\n"

    return (
        row("Limit", "Soft Limit", "Hard Limit", "Units")
        + row("Max nice priority", "0", "0")
        + row("Max processes", soft, hard)
        + row("Max open files", "1024", "524288", "files")
    )


def sealed_topology() -> rtnetlink.Topology:
    """The loopback with its two addresses and the routes the kernel adds for it."""
    return rtnetlink.Topology(
        links=(rtnetlink.Link(LOOPBACK_INDEX, "lo", rtnetlink.IFF_LOOPBACK | LINK_UP),),
        addresses=(
            rtnetlink.Address(
                socket.AF_INET, 8, LOOPBACK_INDEX, ipaddress.IPv4Address("127.0.0.1")
            ),
            rtnetlink.Address(
                socket.AF_INET6, 128, LOOPBACK_INDEX, ipaddress.IPv6Address("::1")
            ),
        ),
        routes=tuple(
            rtnetlink.Route(
                family, 255, prefix_length, LOOPBACK_INDEX, has_next_hop=False
            )
            for family, prefix_length in (
                (socket.AF_INET, 8),
                (socket.AF_INET6, 128),
                (socket.AF_INET6, 8),
            )
        ),
    )


def read_sealed_snapshot(
    root: Path,
    read_topology: Callable[[], rtnetlink.Topology] = sealed_topology,
    *,
    seal: Seal = Seal.COMPOSE,
) -> Snapshot:
    """Read the staged root under `seal`, with the topology `read_topology` gives."""
    return read_snapshot(root, seal, read_topology=read_topology)


def read_staged_root_as_sealed_process(root: Path) -> Snapshot:
    """Read the staged root, with the four process facts of the sealed snapshot."""
    sealed = sealed_snapshot()
    read = read_sealed_snapshot(root)
    return replace(
        read,
        environment_keys=sealed.environment_keys,
        pid=sealed.pid,
        mounts=replace(read.mounts, work_owner_uid=sealed.mounts.work_owner_uid),
        identity=sealed.identity,
    )


def sealed_snapshot() -> Snapshot:
    return Snapshot(
        dumpable=0,
        process_status=SEALED_STATUS,
        mounts=Mounts(
            root_read_only=True,
            mountinfo=SEALED_MOUNTINFO,
            work_mode=0o755,
            work_owner_uid=0,
            docker_socket_present=False,
        ),
        resource_bounds=ResourceBounds(
            pids_max="512",
            memory_max="1073741824",
            process_limit=(str(MAXIMUM_PROCESSES), str(MAXIMUM_PROCESSES)),
        ),
        topology=sealed_topology(),
        identity=Identity(
            uid=0,
            euid=0,
            gid=1000,
            supplementary_gids=(1000,),
            runtime_uid=1000,
            runtime_gid=1000,
            users={
                "presentator-build": worker_self_check.BUILD_IDENTITY,
                "presentator-wire": worker_self_check.WIRE_IDENTITY,
            },
        ),
        environment_keys=frozenset({"PATH", "HOME", "HOSTNAME"}),
        seal=Seal.COMPOSE,
        pid=1,
        cgroup_tasks="1",
        scratch=Scratch(occupied_roots=frozenset(), sysv_tables=SYSV_HEADERS),
        toolchain=Toolchain(
            slidev_mode=stat.S_IFREG | WORLD_READABLE_EXECUTABLE,
            corepack_mode=stat.S_IFDIR | WORLD_READABLE_EXECUTABLE,
            headless_shell_modes=(stat.S_IFREG | WORLD_READABLE_EXECUTABLE,),
            vite_link_target="/work/vite",
        ),
    )


def with_fields(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(snapshot, **changes)


def with_mounts(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, mounts=replace(snapshot.mounts, **changes)
    )


def with_resource_bounds(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(
        snapshot, resource_bounds=replace(snapshot.resource_bounds, **changes)
    )


def stand_in_users(monkeypatch: pytest.MonkeyPatch) -> None:
    users = {
        "presentator": (1000, 1000),
        "presentator-build": worker_self_check.BUILD_IDENTITY,
        "presentator-wire": worker_self_check.WIRE_IDENTITY,
    }

    def password_entry(name: str) -> SimpleNamespace:
        uid, gid = users[name]
        return SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid)

    def group_entry(name: str) -> SimpleNamespace:
        return SimpleNamespace(gr_gid=users[name][1])

    monkeypatch.setattr(worker_self_check.pwd, "getpwnam", password_entry)
    monkeypatch.setattr(worker_self_check.grp, "getgrnam", group_entry)


def stage_sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage the sealed container's files under `tmp_path`, and the reads' stand-ins."""
    stand_in_users(monkeypatch)

    def read_only_root(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(f_flag=os.ST_RDONLY)

    def dumpable_zero(_option: int, _value: int) -> int:
        return 0

    monkeypatch.setattr(worker_self_check.os, "statvfs", read_only_root)
    monkeypatch.setattr(worker_self_check, "libc_prctl", dumpable_zero)

    def file_at(relative: str, content: str = "", mode: int = 0o644) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
        return path

    file_at("proc/self/status", SEALED_STATUS)
    file_at("proc/self/mountinfo", SEALED_MOUNTINFO)
    file_at(
        "proc/self/limits", limits_file(str(MAXIMUM_PROCESSES), str(MAXIMUM_PROCESSES))
    )
    file_at("sys/fs/cgroup/pids.current", "1\n")
    file_at("sys/fs/cgroup/pids.max", "512\n")
    file_at("sys/fs/cgroup/memory.max", "1073741824\n")
    (tmp_path / "work").mkdir()
    (tmp_path / "work").chmod(0o755)
    for scratch_root in ("tmp", "dev/shm", "dev/mqueue"):
        (tmp_path / scratch_root).mkdir(parents=True)
    for table, header in SYSV_HEADERS.items():
        file_at(f"proc/sysvipc/{table}", header)
    (tmp_path / "opt/corepack").mkdir(parents=True)
    (tmp_path / "opt/corepack").chmod(0o755)
    modules = tmp_path / "app/slidev/node_modules"
    file_at("app/slidev/node_modules/.bin/slidev", mode=0o755)
    package = modules / ".pnpm/@slidev+cli@1/node_modules/@slidev/cli"
    (package / "node_modules").mkdir(parents=True)
    (package / "node_modules/.vite").symlink_to(worker_self_check.VITE_CACHE_TARGET)
    (modules / "@slidev").mkdir()
    (modules / "@slidev/cli").symlink_to(package)
    shell = (
        "opt/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64"
    )
    file_at(f"{shell}/chrome-headless-shell", mode=0o755)
    return tmp_path
