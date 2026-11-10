"""The build worker's self-check: the facts it reads and the predicates it judges."""

import ctypes
import grp
import ipaddress
import os
import pwd
import resource
import stat
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path

from presentator.adapters import build_wire, rtnetlink
from presentator.host.config import cannot_start, worker_settings_variables
from presentator.host.worker_scratch import (
    QUEUE_MOUNT,
    ROOT_MODE,
    SHARED_MEMORY_MOUNT,
    TEMPORARY_MOUNT,
    VITE_DIRECTORY,
    WORK_MOUNT,
    Scratch,
    read_scratch,
    scratch_empty,
)

WORKER_CAPABILITIES = 0xE0
MAXIMUM_PROCESSES = 512
RUNTIME_USER = "presentator"
BUILD_USER = "presentator-build"
WIRE_USER = "presentator-wire"
BUILD_IDENTITY = (61001, 61001)
WIRE_IDENTITY = (61002, 61002)
NO_CAPABILITIES = "0" * 16
SOCKET_MOUNT_NAME = build_wire.SOCKET_DIRECTORY.lstrip("/")
CGROUP_DIRECTORY = "sys/fs/cgroup"
PROCESS_LIMITS_FILE = "proc/self/limits"
PROCESS_LIMIT_ROW = "Max processes"
TMPFS_MOUNT_NAMES = (
    TEMPORARY_MOUNT.lstrip("/"),
    SHARED_MEMORY_MOUNT.lstrip("/"),
    SOCKET_MOUNT_NAME,
)
TMPFS_MOUNTS = tuple(
    str(Path("/") / name) for name in (WORK_MOUNT.lstrip("/"), *TMPFS_MOUNT_NAMES)
)
NO_EXECUTION_MOUNTS = TMPFS_MOUNTS[:2]
GROUP_AND_OTHERS_WRITE = stat.S_IWGRP | stat.S_IWOTH
HARDENED_MOUNT_OPTIONS = frozenset({"nosuid", "nodev", "noexec"})
DOCKER_SOCKETS = ("/run/docker.sock", "/var/run/docker.sock")
TOOLCHAIN_PROJECT = "app/slidev"
SLIDEV_BINARY = f"{TOOLCHAIN_PROJECT}/node_modules/.bin/slidev"
SLIDEV_PACKAGE = f"{TOOLCHAIN_PROJECT}/node_modules/@slidev/cli"
COREPACK_DIRECTORY = "opt/corepack"
PLAYWRIGHT_DIRECTORY = "opt/ms-playwright"
HEADLESS_SHELL_PATTERN = (
    f"{PLAYWRIGHT_DIRECTORY}/chromium_headless_shell-*/"
    "chrome-headless-shell-linux64/chrome-headless-shell"
)
VITE_CACHE_TARGET = f"{WORK_MOUNT}/{VITE_DIRECTORY}"
SEAL_VARIABLE = "PRESENTATOR_WORKER_SEAL"
# The names the kubelet injects into every container for the `kubernetes` service.
KUBERNETES_ENVIRONMENT_KEYS = frozenset(
    {
        "KUBERNETES_SERVICE_HOST",
        "KUBERNETES_SERVICE_PORT",
        "KUBERNETES_SERVICE_PORT_HTTPS",
        "KUBERNETES_PORT",
        "KUBERNETES_PORT_443_TCP",
        "KUBERNETES_PORT_443_TCP_PROTO",
        "KUBERNETES_PORT_443_TCP_PORT",
        "KUBERNETES_PORT_443_TCP_ADDR",
    }
)
# The image's and its base's keys, the runtime's HOSTNAME, Python's own LC_CTYPE.
ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "HOME",
        "PLAYWRIGHT_BROWSERS_PATH",
        "COREPACK_HOME",
        "UV_PROJECT_ENVIRONMENT",
        "UV_PYTHON_INSTALL_DIR",
        "UV_COMPILE_BYTECODE",
        "UV_NO_CACHE",
        "PATH",
        "PRESENTATOR_DATABASE",
        "PRESENTATOR_MIRRORS",
        "PRESENTATOR_BUILDS",
        "PRESENTATOR_TOOLCHAIN",
        "PRESENTATOR_LOBBY_BUILD",
        "PRESENTATOR_HOST",
        "PRESENTATOR_LOCAL_SOURCES_MOUNT",
        "NODE_VERSION",
        "YARN_VERSION",
    }
    | {"HOSTNAME", "LC_CTYPE", SEAL_VARIABLE}
    | worker_settings_variables()
)

LOOPBACK_NAME = "lo"
LOOPBACK_ADDRESSES = frozenset(
    {
        (ipaddress.IPv4Address("127.0.0.1"), 8),
        (ipaddress.IPv6Address("::1"), 128),
    }
)
PR_GET_DUMPABLE = 3
PR_SET_DUMPABLE = 4


class Predicate(StrEnum):
    """A self-check predicate, named in refusal order."""

    NON_DUMPABLE = "non-dumpable"
    CAPABILITIES = "capabilities"
    MOUNTS = "mounts"
    NO_ROUTE = "no-route"
    RESOURCE_BOUNDS = "resource-bounds"
    IDENTITIES = "identities"
    ENVIRONMENT = "environment"
    PID_ONE = "pid-one"
    ALONE = "alone"
    SCRATCH = "scratch"
    TOOLCHAIN = "toolchain"
    KUBERNETES_UNPROVEN = "kubernetes-unproven"


class Seal(StrEnum):
    """The container shape the worker is sealed for."""

    COMPOSE = "compose"
    KUBERNETES = "kubernetes"


@dataclass(frozen=True)
class Identity:
    """The process identity and the users the image created."""

    uid: int
    euid: int
    gid: int
    supplementary_gids: tuple[int, ...]
    runtime_uid: int
    runtime_gid: int
    users: Mapping[str, tuple[int, int]]


@dataclass(frozen=True)
class Toolchain:
    """What the sealed container holds of the build toolchain."""

    slidev_mode: int | None
    corepack_mode: int | None
    headless_shell_modes: tuple[int | None, ...]
    vite_link_target: str | None


@dataclass(frozen=True)
class Snapshot:
    """Everything the self-check reads, taken once."""

    dumpable: int
    process_status: str
    root_read_only: bool
    mountinfo: str
    work_mode: int
    work_owner_uid: int
    topology: rtnetlink.Topology
    docker_socket_present: bool
    pids_max: str
    memory_max: str
    process_limit: tuple[str, str]
    identity: Identity
    environment_keys: frozenset[str]
    seal: Seal
    pid: int
    cgroup_tasks: str
    scratch: Scratch
    toolchain: Toolchain
    unreadable: frozenset[Predicate] = frozenset()


def status_fields(process_status: str) -> dict[str, str]:
    """The name and value of each line of a process status file."""
    fields = (line.partition(":") for line in process_status.splitlines())
    return {name: value.strip() for name, _, value in fields}


def _capabilities_sealed(snapshot: Snapshot) -> bool:
    fields = status_fields(snapshot.process_status)
    worker = f"{WORKER_CAPABILITIES:016x}"
    return (
        fields.get("CapEff") == worker
        and fields.get("CapPrm") == worker
        and fields.get("CapInh") == NO_CAPABILITIES
        and fields.get("CapAmb") == NO_CAPABILITIES
        and int(fields.get("CapBnd", "-1"), 16) & ~WORKER_CAPABILITIES == 0
        and fields.get("NoNewPrivs") == "1"
        and fields.get("Seccomp") == "2"
    )


def _mount_table(mountinfo: str) -> dict[str, tuple[str, frozenset[str]]]:
    table: dict[str, tuple[str, frozenset[str]]] = {}
    for line in mountinfo.splitlines():
        head, _, tail = line.partition(" - ")
        fields = head.split()
        table[fields[4]] = (tail.split()[0], frozenset(fields[5].split(",")))
    return table


def _mount_table_sealed(mountinfo: str) -> bool:
    table = _mount_table(mountinfo)
    return (
        all(mount in table and table[mount][0] == "tmpfs" for mount in TMPFS_MOUNTS)
        and QUEUE_MOUNT in table
        and table[QUEUE_MOUNT][0] == "mqueue"
        and all(
            table[mount][1] >= HARDENED_MOUNT_OPTIONS for mount in NO_EXECUTION_MOUNTS
        )
    )


def _mounts_sealed(snapshot: Snapshot) -> bool:
    return (
        snapshot.root_read_only
        and _mount_table_sealed(snapshot.mountinfo)
        and snapshot.work_mode == ROOT_MODE
        and snapshot.work_owner_uid == 0
        and not snapshot.docker_socket_present
    )


def _no_route(snapshot: Snapshot) -> bool:
    topology = snapshot.topology
    if len(topology.links) != 1:
        return False
    [link] = topology.links
    return (
        link.name == LOOPBACK_NAME
        and bool(link.flags & rtnetlink.IFF_LOOPBACK)
        and all(
            found.link_index == link.index
            and (found.ip_address, found.prefix_length) in LOOPBACK_ADDRESSES
            for found in topology.addresses
        )
        and all(
            found.output_link_index == link.index
            and found.destination_prefix_length > 0
            and not found.has_next_hop
            for found in topology.routes
        )
    )


def _within_maximum(value: str) -> bool:
    return value.isdecimal() and int(value) <= MAXIMUM_PROCESSES


def _resources_bounded(snapshot: Snapshot) -> bool:
    if snapshot.seal is Seal.KUBERNETES:
        bounded = all(map(_within_maximum, snapshot.process_limit))
    else:
        bounded = _within_maximum(snapshot.pids_max)
    return bounded and snapshot.memory_max.isdecimal()


def _identities_sealed(snapshot: Snapshot) -> bool:
    identity = snapshot.identity
    build_user = identity.users.get(BUILD_USER)
    wire_user = identity.users.get(WIRE_USER)
    unusable = {0, identity.runtime_uid, identity.runtime_gid}
    return (
        identity.uid == 0
        and identity.euid == 0
        and identity.gid == identity.runtime_gid
        and identity.supplementary_gids == (identity.runtime_gid,)
        and build_user == BUILD_IDENTITY
        and wire_user == WIRE_IDENTITY
        and not unusable & {*BUILD_IDENTITY, *WIRE_IDENTITY}
    )


def seal_from_environment(environment: Mapping[str, str]) -> Seal:
    """The seal the environment selects; unset means Compose.

    The refusal names the variable and never the value read.
    """
    try:
        return Seal(environment.get(SEAL_VARIABLE, Seal.COMPOSE))
    except ValueError:
        accepted = " or ".join(Seal)
        reason = f"{SEAL_VARIABLE}: is {accepted}, or unset"
        raise cannot_start(reason) from None


def _environment_allowed(snapshot: Snapshot) -> bool:
    allowed = ALLOWED_ENVIRONMENT_KEYS
    if snapshot.seal is Seal.KUBERNETES:
        allowed = allowed | KUBERNETES_ENVIRONMENT_KEYS
    return snapshot.environment_keys <= allowed


def _is_pid_one(snapshot: Snapshot) -> bool:
    return snapshot.pid == 1


def _is_alone(snapshot: Snapshot) -> bool:
    return snapshot.cgroup_tasks == "1"


def _toolchain_present(snapshot: Snapshot) -> bool:
    toolchain = snapshot.toolchain
    entries = (
        toolchain.slidev_mode,
        toolchain.corepack_mode,
        *toolchain.headless_shell_modes,
    )
    return (
        len(toolchain.headless_shell_modes) == 1
        and all(
            mode is not None
            and mode & stat.S_IXOTH
            and not mode & GROUP_AND_OTHERS_WRITE
            for mode in entries
        )
        and toolchain.vite_link_target == VITE_CACHE_TARGET
    )


SELF_CHECK: tuple[tuple[Predicate, Callable[[Snapshot], bool]], ...] = (
    (Predicate.NON_DUMPABLE, lambda snapshot: snapshot.dumpable == 0),
    (Predicate.CAPABILITIES, _capabilities_sealed),
    (Predicate.MOUNTS, _mounts_sealed),
    (Predicate.NO_ROUTE, _no_route),
    (Predicate.RESOURCE_BOUNDS, _resources_bounded),
    (Predicate.IDENTITIES, _identities_sealed),
    (Predicate.ENVIRONMENT, _environment_allowed),
    (Predicate.PID_ONE, _is_pid_one),
    (Predicate.ALONE, _is_alone),
    (Predicate.SCRATCH, lambda snapshot: scratch_empty(snapshot.scratch)),
    (Predicate.TOOLCHAIN, _toolchain_present),
    (Predicate.KUBERNETES_UNPROVEN, lambda snapshot: snapshot.seal is Seal.COMPOSE),
)


def _holds(
    predicate: Predicate, judge: Callable[[Snapshot], bool], snapshot: Snapshot
) -> bool:
    if predicate in snapshot.unreadable:
        return False
    try:
        return judge(snapshot)
    except ValueError, IndexError:
        return False


def first_failing_predicate(snapshot: Snapshot) -> Predicate | None:
    """The first predicate, in refusal order, that the snapshot fails.

    A predicate whose facts could not be read or parsed fails.
    """
    return next(
        (name for name, judge in SELF_CHECK if not _holds(name, judge, snapshot)),
        None,
    )


def _capped(limit: int, ceiling: int) -> int:
    return ceiling if limit == resource.RLIM_INFINITY else min(limit, ceiling)


def lowered_process_limit(soft: int, hard: int) -> tuple[int, int]:
    """The soft and hard process limit lowered to at most the maximum, never raised."""
    lowered_hard = _capped(hard, MAXIMUM_PROCESSES)
    return _capped(soft, lowered_hard), lowered_hard


def libc_prctl(option: int, value: int) -> int:
    """Call prctl, raising the errno it sets on failure."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, *[ctypes.c_ulong] * 4]
    libc.prctl.restype = ctypes.c_int
    result = libc.prctl(option, value, 0, 0, 0)
    if result < 0:
        raise OSError(ctypes.get_errno(), "prctl failed")
    return result


def _mode_if_file_or_directory(path: Path) -> int | None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return None
    return mode if stat.S_ISREG(mode) or stat.S_ISDIR(mode) else None


def _vite_link_target(root: Path) -> str | None:
    vite = root / SLIDEV_PACKAGE / "node_modules" / ".vite"
    try:
        return str(vite.readlink())
    except OSError:
        return None


def _read_toolchain(root: Path) -> Toolchain:
    slidev_binary = root / SLIDEV_BINARY
    return Toolchain(
        slidev_mode=_mode_if_file_or_directory(slidev_binary),
        corepack_mode=_mode_if_file_or_directory(root / COREPACK_DIRECTORY),
        headless_shell_modes=tuple(
            map(_mode_if_file_or_directory, root.glob(HEADLESS_SHELL_PATTERN))
        ),
        vite_link_target=_vite_link_target(root),
    )


def _read_identity() -> Identity:
    runtime = pwd.getpwnam(RUNTIME_USER)
    users = {
        user.pw_name: (user.pw_uid, user.pw_gid)
        for user in map(pwd.getpwnam, (BUILD_USER, WIRE_USER))
    }
    return Identity(
        uid=os.getuid(),
        euid=os.geteuid(),
        gid=os.getgid(),
        supplementary_gids=tuple(os.getgroups()),
        runtime_uid=runtime.pw_uid,
        runtime_gid=grp.getgrnam(RUNTIME_USER).gr_gid,
        users=users,
    )


def lstat_if_present(path: Path) -> os.stat_result | None:
    """The lstat of `path`, or None when it does not exist."""
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _read_mount_facts(root: Path) -> tuple[bool, str, int, int, bool]:
    work = (root / WORK_MOUNT.lstrip("/")).stat()
    return (
        bool(os.statvfs(root).f_flag & os.ST_RDONLY),
        (root / "proc/self/mountinfo").read_text(),
        stat.S_IMODE(work.st_mode),
        work.st_uid,
        any(
            lstat_if_present(root / docker_socket.lstrip("/")) is not None
            for docker_socket in DOCKER_SOCKETS
        ),
    )


def _read_cgroup_file(root: Path, name: str) -> str:
    return (root / CGROUP_DIRECTORY / name).read_text().strip()


def _read_process_limit(root: Path) -> tuple[str, str]:
    for line in (root / PROCESS_LIMITS_FILE).read_text().splitlines():
        if line.startswith(PROCESS_LIMIT_ROW):
            soft, hard, *_ = [*line.removeprefix(PROCESS_LIMIT_ROW).split(), "", ""]
            if hard:
                return soft, hard
    raise KeyError(PROCESS_LIMIT_ROW)


def _read_resource_bounds(root: Path) -> tuple[str, str, tuple[str, str]]:
    return (
        _read_cgroup_file(root, "pids.max"),
        _read_cgroup_file(root, "memory.max"),
        _read_process_limit(root),
    )


UNREAD_RESOURCE_BOUNDS = ("", "", ("", ""))
UNREAD_IDENTITY = Identity(-1, -1, -1, (), -1, -1, {})
UNREAD_TOOLCHAIN = Toolchain(None, None, (), None)
UNREAD_SCRATCH = Scratch(frozenset(), {})
UNREAD_TOPOLOGY = rtnetlink.Topology((), (), ())


class _FactReader:
    """Reads facts, remembering the predicate of each one that failed to read."""

    def __init__(self) -> None:
        self.unreadable: set[Predicate] = set()

    def read[Fact](
        self, predicate: Predicate, reading: Callable[[], Fact], placeholder: Fact
    ) -> Fact:
        try:
            return reading()
        except OSError, KeyError, UnicodeDecodeError:
            self.unreadable.add(predicate)
            return placeholder


def read_snapshot(
    root: Path, seal: Seal, *, read_topology: Callable[[], rtnetlink.Topology]
) -> Snapshot:
    """Read the facts the predicates judge, from the filesystem under `root`.

    A fact that cannot be read or decoded marks its predicate unreadable, so the
    self-check judges every predicate in order. The container's task count is
    read first and the scratch places right after it: no other fact is read and
    no child is launched in between. The network topology is read after both.
    """
    facts = _FactReader()
    read = facts.read
    cgroup_tasks = read(
        Predicate.ALONE, partial(_read_cgroup_file, root, "pids.current"), ""
    )
    scratch = read(Predicate.SCRATCH, partial(read_scratch, root), UNREAD_SCRATCH)
    dumpable = read(Predicate.NON_DUMPABLE, partial(libc_prctl, PR_GET_DUMPABLE, 0), -1)
    process_status = read(
        Predicate.CAPABILITIES, (root / "proc/self/status").read_text, ""
    )
    (
        root_read_only,
        mountinfo,
        work_mode,
        work_owner_uid,
        socket_present,
    ) = read(
        Predicate.MOUNTS,
        partial(_read_mount_facts, root),
        (False, "", 0, -1, True),
    )
    pids_max, memory_max, process_limit = read(
        Predicate.RESOURCE_BOUNDS,
        partial(_read_resource_bounds, root),
        UNREAD_RESOURCE_BOUNDS,
    )
    return Snapshot(
        dumpable=dumpable,
        process_status=process_status,
        root_read_only=root_read_only,
        mountinfo=mountinfo,
        work_mode=work_mode,
        work_owner_uid=work_owner_uid,
        topology=read(Predicate.NO_ROUTE, read_topology, UNREAD_TOPOLOGY),
        docker_socket_present=socket_present,
        pids_max=pids_max,
        memory_max=memory_max,
        process_limit=process_limit,
        identity=read(Predicate.IDENTITIES, _read_identity, UNREAD_IDENTITY),
        environment_keys=frozenset(os.environ),
        seal=seal,
        pid=os.getpid(),
        cgroup_tasks=cgroup_tasks,
        scratch=scratch,
        toolchain=read(
            Predicate.TOOLCHAIN, partial(_read_toolchain, root), UNREAD_TOOLCHAIN
        ),
        unreadable=frozenset(facts.unreadable),
    )
