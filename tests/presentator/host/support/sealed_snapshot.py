"""The sealed-container snapshot the worker self-check passes, and a way to alter it."""

import stat
from collections.abc import Callable
from dataclasses import replace

from presentator.host import worker
from presentator.host.worker import Identity, Snapshot, Toolchain

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
SEALED_MOUNTINFO = "20 1 0:20 / / ro,relatime - overlay overlay rw\n" + "".join(
    f"{21 + index} 20 0:{21 + index} / {mount} {HARDENED} - tmpfs tmpfs rw\n"
    for index, mount in enumerate(worker.TMPFS_MOUNTS)
)
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
        toolchain=Toolchain(
            slidev_mode=stat.S_IFREG | WORLD_READABLE_EXECUTABLE,
            corepack_mode=stat.S_IFDIR | WORLD_READABLE_EXECUTABLE,
            headless_shell_modes=(stat.S_IFREG | WORLD_READABLE_EXECUTABLE,),
            vite_link_target="/work/vite",
        ),
    )


def with_fields(**changes: object) -> Callable[[Snapshot], Snapshot]:
    return lambda snapshot: replace(snapshot, **changes)
