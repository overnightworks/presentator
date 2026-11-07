"""The sealed-container snapshot the worker self-check passes, and a way to alter it."""

import os
import stat
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from presentator.host import worker
from presentator.host.worker import Identity, Snapshot, Toolchain
from presentator.host.worker_scratch import QUEUE_MOUNT, Scratch

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
        for index, mount in enumerate(worker.TMPFS_MOUNTS)
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
NETWORK_HEADER = "Inter-|   Receive\n face |bytes\n"
LOOPBACK_ONLY = NETWORK_HEADER + "    lo: 0 0\n"
WORLD_READABLE_EXECUTABLE = 0o755


def sealed_snapshot() -> Snapshot:
    return Snapshot(
        dumpable=0,
        process_status=SEALED_STATUS,
        root_read_only=True,
        mountinfo=SEALED_MOUNTINFO,
        work_mode=0o755,
        work_owner_uid=0,
        network_devices=LOOPBACK_ONLY,
        docker_socket_present=False,
        pids_max="512",
        memory_max="1073741824",
        identity=Identity(
            uid=0,
            euid=0,
            gid=1000,
            supplementary_gids=(1000,),
            runtime_uid=1000,
            runtime_gid=1000,
            users={
                "presentator-build": (10001, 10001),
                "presentator-wire": (10002, 10002),
            },
        ),
        environment_keys=frozenset({"PATH", "HOME", "HOSTNAME"}),
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


def stand_in_users(monkeypatch: pytest.MonkeyPatch) -> None:
    users = {
        "presentator": (1000, 1000),
        "presentator-build": (10001, 10001),
        "presentator-wire": (10002, 10002),
    }

    def password_entry(name: str) -> SimpleNamespace:
        uid, gid = users[name]
        return SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid)

    def group_entry(name: str) -> SimpleNamespace:
        return SimpleNamespace(gr_gid=users[name][1])

    monkeypatch.setattr(worker.pwd, "getpwnam", password_entry)
    monkeypatch.setattr(worker.grp, "getgrnam", group_entry)


def stage_sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Stage the sealed container's files under `tmp_path`, and the reads' stand-ins."""
    stand_in_users(monkeypatch)

    def read_only_root(_root: Path) -> SimpleNamespace:
        return SimpleNamespace(f_flag=os.ST_RDONLY)

    def dumpable_zero(_option: int, _value: int) -> int:
        return 0

    monkeypatch.setattr(worker.os, "statvfs", read_only_root)
    monkeypatch.setattr(worker, "libc_prctl", dumpable_zero)

    def file_at(relative: str, content: str = "", mode: int = 0o644) -> Path:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(mode)
        return path

    file_at("proc/self/status", SEALED_STATUS)
    file_at("proc/self/mountinfo", SEALED_MOUNTINFO)
    file_at("proc/net/dev", LOOPBACK_ONLY)
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
    (package / "node_modules/.vite").symlink_to("/work/vite")
    (modules / "@slidev").mkdir()
    (modules / "@slidev/cli").symlink_to(package)
    shell = (
        "opt/ms-playwright/chromium_headless_shell-1243/chrome-headless-shell-linux64"
    )
    file_at(f"{shell}/chrome-headless-shell", mode=0o755)
    return tmp_path
