"""The build worker: it refuses to start outside the sealed container, then serves."""

import argparse
import ctypes
import fcntl
import logging
import os
import resource
import signal
import socket
import stat
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntFlag, StrEnum
from functools import partial
from pathlib import Path
from typing import Protocol

from presentator.adapters import build_wire, rtnetlink
from presentator.adapters.worker_children import (
    ChildIdentity,
    readable_within,
    spawn_gated,
)
from presentator.host.config import (
    ConfigurationError,
    WorkerSettings,
    load_worker_settings,
)
from presentator.host.worker_scratch import (
    HOME_DIRECTORY,
    TEMPORARY_MOUNT,
    WORK_MOUNT,
    close_deck,
    make_open_directory,
    prepare_step_paths,
)
from presentator.host.worker_self_check import (
    BUILD_IDENTITY,
    COREPACK_DIRECTORY,
    MOUNT_ROOT_MODES,
    NO_CAPABILITIES,
    PLAYWRIGHT_DIRECTORY,
    PR_SET_DUMPABLE,
    TOOLCHAIN_PROJECT,
    WIRE_IDENTITY,
    Predicate,
    Seal,
    Snapshot,
    first_failing_predicate,
    libc_prctl,
    lowered_process_limit,
    lstat_if_present,
    process_limit_bounds,
    read_snapshot,
    seal_from_environment,
    status_fields,
)
from presentator.host.worker_serving import (
    Serving,
    StepEffects,
    accept_connections,
)
from presentator.host.worker_termination import (
    FaultHandlerWatchdog,
    SweepEffects,
    list_process_ids,
    reap_children,
    sweep,
)

logger = logging.getLogger(__name__)

REFUSAL_EXIT_STATUS = 2
BIND_UMASK = 0o117
LISTEN_BACKLOG = 4
ONLY_THE_OWNER = 0o077


class StartRefusal(StrEnum):
    """A serving step that refused the start."""

    SOCKET_DIRECTORY = "socket-directory"
    STALE_SOCKET = "stale-socket"
    BIND = "bind"
    LISTEN = "listen"
    SETTINGS = "settings"


class StartRefusedError(Exception):
    """A serving step refused; it carries the step's name."""

    def __init__(self, refusal: StartRefusal) -> None:
        """Remember which step refused."""
        super().__init__(refusal)
        self.refusal = refusal


class Server(Protocol):
    """Serves the socket for the runtime uid and the controller group."""

    def __call__(
        self, *, controller_gid: int, runtime_uid: int, settings: WorkerSettings
    ) -> int:
        """Run until a refusal and return its exit status."""
        ...


@dataclass(frozen=True)
class Boundary:
    """The process effects and reads the start-up steps go through."""

    set_non_dumpable: Callable[[], None]
    set_umask: Callable[[], None]
    read_seal: Callable[[], Seal]
    lower_process_limit: Callable[[], None]
    fix_up_mounts: Callable[[], None]
    read_snapshot: Callable[[Seal], Snapshot]
    load_settings: Callable[[], WorkerSettings]
    serve: Server


def _set_non_dumpable() -> None:
    libc_prctl(PR_SET_DUMPABLE, 0)


def lower_process_limit(
    get_limit: Callable[[], tuple[int, int]],
    set_limit: Callable[[tuple[int, int]], None],
) -> None:
    """Set the process limit to its lowered form."""
    set_limit(lowered_process_limit(*get_limit()))


def fix_up_mounts(root: Path) -> None:
    """Set each mount root under `root` to the mode `mounts` requires of it.

    A root is opened without following a link and without reading what lies
    below it; any failure propagates.
    """
    for mount, mode in MOUNT_ROOT_MODES.items():
        descriptor = os.open(
            root / mount.lstrip("/"), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        try:
            os.fchmod(descriptor, mode)
        finally:
            os.close(descriptor)


def _set_umask() -> None:
    os.umask(ONLY_THE_OWNER)


def _fix_up_socket_directory(
    directory: Path, controller_uid: int, controller_gid: int
) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            if os.fstat(descriptor).st_uid != controller_uid:
                raise StartRefusedError(StartRefusal.SOCKET_DIRECTORY)
            os.fchown(descriptor, -1, controller_gid)
            os.fchmod(descriptor, build_wire.SOCKET_DIRECTORY_MODE)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise StartRefusedError(StartRefusal.SOCKET_DIRECTORY) from error


def remove_stale_socket(socket_path: Path, controller_uid: int) -> None:
    """Unlink a socket the controller owns; refuse any other entry."""
    try:
        status = lstat_if_present(socket_path)
        if status is None:
            return
        if not (stat.S_ISSOCK(status.st_mode) and status.st_uid == controller_uid):
            raise StartRefusedError(StartRefusal.STALE_SOCKET)
        socket_path.unlink()
    except OSError as error:
        raise StartRefusedError(StartRefusal.STALE_SOCKET) from error


def prepare_socket_directory(
    directory: Path, controller_uid: int, controller_gid: int
) -> None:
    """Step 4: fix up the volume root and clear a stale socket."""
    _fix_up_socket_directory(directory, controller_uid, controller_gid)
    remove_stale_socket(directory / build_wire.SOCKET_NAME, controller_uid)


def bind_socket(
    directory: Path, controller_uid: int, controller_gid: int
) -> socket.socket:
    """Step 5: bind under a narrow umask and check what the bind created."""
    try:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        raise StartRefusedError(StartRefusal.BIND) from error
    socket_path = directory / build_wire.SOCKET_NAME
    try:
        os.umask(BIND_UMASK)
        try:
            listener.bind(str(socket_path))
        finally:
            _set_umask()
        status = socket_path.lstat()
    except OSError as error:
        listener.close()
        raise StartRefusedError(StartRefusal.BIND) from error
    created_as_expected = (
        stat.S_ISSOCK(status.st_mode)
        and (status.st_uid, status.st_gid) == (controller_uid, controller_gid)
        and stat.S_IMODE(status.st_mode) == build_wire.SOCKET_MODE
    )
    if not created_as_expected:
        listener.close()
        raise StartRefusedError(StartRefusal.BIND)
    return listener


def start_listening(listener: socket.socket) -> None:
    """Step 6: from here on a connect is queued rather than refused."""
    try:
        listener.listen(LISTEN_BACKLOG)
    except OSError as error:
        raise StartRefusedError(StartRefusal.LISTEN) from error


def child_gate_admits(process_status: str, identity: tuple[int, int]) -> bool:
    """A child may be released only as the identity, unprivileged and sealed."""
    uid, gid = identity
    fields = status_fields(process_status)
    return (
        fields.get("Uid", "").split() == [str(uid)] * 4
        and fields.get("Gid", "").split() == [str(gid)] * 4
        and fields.get("Groups") == ""
        and all(
            fields.get(name) == NO_CAPABILITIES
            for name in ("CapInh", "CapPrm", "CapEff", "CapAmb")
        )
        and fields.get("NoNewPrivs") == "1"
        and fields.get("Seccomp") == "2"
    )


def serve(
    directory: Path,
    *,
    controller_uid: int,
    controller_gid: int,
    runtime_uid: int,
    serving: Serving,
) -> int:
    """Run start-up steps 4 to 6, then accept until the life ends."""
    try:
        prepare_socket_directory(directory, controller_uid, controller_gid)
        listener = bind_socket(directory, controller_uid, controller_gid)
        with listener:
            start_listening(listener)
            life_end = accept_connections(listener, runtime_uid, serving)
    except StartRefusedError as refused:
        return _refuse(refused.refusal)
    logger.log(life_end.log_level, "life ended: %s", life_end)
    return 0


# The Python standard library lacks these Linux constants (linux/fcntl.h).
F_ADD_SEALS = 1033
MFD_CLOEXEC = 1
MFD_ALLOW_SEALING = 2


class RecordSeal(IntFlag):
    """The memfd seals that freeze the request record."""

    SEAL = 1
    SHRINK = 2
    GROW = 4
    WRITE = 8


RECORD_SEALS = RecordSeal.WRITE | RecordSeal.GROW | RecordSeal.SHRINK | RecordSeal.SEAL


def create_request_record() -> int:
    """A sealable, close-on-exec memfd; the standalone Python has no os.memfd_create."""
    libc = ctypes.CDLL(None, use_errno=True)
    libc.memfd_create.argtypes = [ctypes.c_char_p, ctypes.c_uint]
    libc.memfd_create.restype = ctypes.c_int
    descriptor = libc.memfd_create(b"request-record", MFD_CLOEXEC | MFD_ALLOW_SEALING)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "memfd_create failed")
    return descriptor


def seal_record(descriptor: int) -> None:
    """Freeze the request record; EBUSY while a writable shared mapping exists."""
    fcntl.fcntl(descriptor, F_ADD_SEALS, RECORD_SEALS)


def real_serving(settings: WorkerSettings) -> Serving:
    """The effects of a serving life on this machine, bounded by the settings."""
    return Serving(
        spawn=partial(
            spawn_gated, identity=ChildIdentity(*WIRE_IDENTITY, extra_groups=())
        ),
        gate_admits=partial(child_gate_admits, identity=WIRE_IDENTITY),
        sweep=partial(
            sweep,
            SweepEffects(
                kill_all=partial(os.kill, -1, signal.SIGKILL),
                reap=reap_children,
                list_process_ids=partial(list_process_ids, Path("/proc")),
                clock=time.monotonic,
                sleep=time.sleep,
            ),
        ),
        create_record=create_request_record,
        seal=seal_record,
        open_deck=make_open_directory,
        close_deck=close_deck,
        work=Path(WORK_MOUNT),
        step=StepEffects(
            spawn=partial(
                spawn_gated, identity=ChildIdentity(*BUILD_IDENTITY, extra_groups=())
            ),
            gate_admits=partial(child_gate_admits, identity=BUILD_IDENTITY),
            prepare=prepare_step_paths,
            environment={
                "PATH": os.environ["PATH"],
                "HOME": f"{WORK_MOUNT}/{HOME_DIRECTORY}",
                "TMPDIR": TEMPORARY_MOUNT,
                "PLAYWRIGHT_BROWSERS_PATH": f"/{PLAYWRIGHT_DIRECTORY}",
                "COREPACK_HOME": f"/{COREPACK_DIRECTORY}",
                "COREPACK_ENABLE_NETWORK": "0",
            },
            project=Path("/") / TOOLCHAIN_PROJECT,
        ),
        watchdog=FaultHandlerWatchdog(),
        clock=time.monotonic,
        readable=readable_within,
        build_timeout_seconds=settings.build_timeout_seconds,
        build_output_megabytes=settings.build_output_megabytes,
    )


def _serve_on_this_machine(
    *, controller_gid: int, runtime_uid: int, settings: WorkerSettings
) -> int:
    return serve(
        Path(build_wire.SOCKET_DIRECTORY),
        controller_uid=0,
        controller_gid=controller_gid,
        runtime_uid=runtime_uid,
        serving=real_serving(settings),
    )


REAL_BOUNDARY = Boundary(
    _set_non_dumpable,
    _set_umask,
    partial(seal_from_environment, os.environ),
    partial(
        lower_process_limit,
        partial(resource.getrlimit, resource.RLIMIT_NPROC),
        partial(resource.setrlimit, resource.RLIMIT_NPROC),
    ),
    partial(fix_up_mounts, Path("/")),
    partial(read_snapshot, Path("/"), read_topology=rtnetlink.read_topology),
    load_worker_settings,
    _serve_on_this_machine,
)


def _refuse(refused: Predicate | StartRefusal) -> int:
    logger.error("start refused: %s", refused)
    return REFUSAL_EXIT_STATUS


def _refuse_settings(error: ConfigurationError) -> int:
    logger.error("settings refused", exc_info=error)
    return _refuse(StartRefusal.SETTINGS)


def _process_limit_lowered(boundary: Boundary, seal: Seal) -> bool:
    if not process_limit_bounds(seal):
        return True
    try:
        boundary.lower_process_limit()
    except OSError, ValueError:
        return False
    return True


def _mounts_fixed_up(boundary: Boundary, seal: Seal) -> bool:
    if seal is not Seal.KUBERNETES:
        return True
    try:
        boundary.fix_up_mounts()
    except OSError:
        return False
    return True


def _self_checked(boundary: Boundary, seal: Seal) -> Snapshot | Predicate:
    """The snapshot of a start that passes the self-check, else the predicate."""
    if not _process_limit_lowered(boundary, seal):
        return Predicate.RESOURCE_BOUNDS
    if not _mounts_fixed_up(boundary, seal):
        return Predicate.MOUNTS
    snapshot = boundary.read_snapshot(seal)
    return first_failing_predicate(snapshot) or snapshot


def main(argv: Sequence[str] | None = None, boundary: Boundary = REAL_BOUNDARY) -> int:
    """Run the start-up steps; exit status 2 names a refusal."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="exit after the self-check"
    )
    arguments = parser.parse_args(argv)
    try:
        boundary.set_non_dumpable()
    except OSError:
        return _refuse(Predicate.NON_DUMPABLE)
    boundary.set_umask()
    try:
        seal = boundary.read_seal()
    except ConfigurationError as error:
        return _refuse_settings(error)
    snapshot = _self_checked(boundary, seal)
    if isinstance(snapshot, Predicate):
        return _refuse(snapshot)
    if arguments.check:
        return 0
    try:
        settings = boundary.load_settings()
    except ConfigurationError as error:
        return _refuse_settings(error)
    return boundary.serve(
        controller_gid=snapshot.identity.runtime_gid,
        runtime_uid=snapshot.identity.runtime_uid,
        settings=settings,
    )
