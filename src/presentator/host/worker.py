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
from typing import Final, Protocol

import logline

from presentator.adapters.worker_children import (
    ChildIdentity,
    program_output_as,
    readable_within,
    spawn_gated,
)
from presentator.host.environment import ConfigurationError
from presentator.host.lifecycle import (
    StopLine,
    read_command_line,
    refuse_argument,
    signals_end_as_a_stop,
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
    Snapshot,
    first_failing_predicate,
    libc_prctl,
    lowered_process_limit,
    read_snapshot,
    send_probes,
    status_fields,
)
from presentator.host.worker_serving import (
    Serving,
    StepEffects,
    accept_connections,
)
from presentator.host.worker_settings import (
    MINIMUM_KEY_LENGTH,
    WorkerSettings,
    load_worker_settings,
)
from presentator.host.worker_termination import (
    FaultHandlerWatchdog,
    SweepEffects,
    list_process_ids,
    reap_children,
    sweep,
)
from presentator.host.worker_wire import SERVICE_NAME

logger = logging.getLogger(__name__)

START_REFUSED_EVENT: Final = "start.refused"
REFUSAL_EXIT_STATUS = 2
_PORT: Final = "--port"
_KEY_FILE: Final = "--key-file"
NOT_READY_EXIT_STATUS = 1
LISTEN_BACKLOG = 4
ONLY_THE_OWNER = 0o077
KEY_MODE_MASK = 0o077
MAXIMUM_KEY_BYTES = 4096
SOCKET_TABLE = Path("/proc/net/tcp")
LISTENING = "0A"
ESTABLISHED = "01"


class StartRefusal(StrEnum):
    """A serving step that refused the start."""

    KEY = "key"
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
    """Serves the port to the peers that hold the key."""

    def __call__(
        self,
        *,
        port: int,
        key_file: Path,
        settings: WorkerSettings,
        stop_line: StopLine,
    ) -> int:
        """Run until a refusal or the life's end, writing `stop_line` at the end."""
        ...


@dataclass(frozen=True)
class Boundary:
    """The process effects and reads the start-up steps go through."""

    set_non_dumpable: Callable[[], None]
    set_umask: Callable[[], None]
    lower_process_limit: Callable[[], None]
    fix_up_mounts: Callable[[], None]
    read_snapshot: Callable[[], Snapshot]
    read_socket_table: Callable[[], str]
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


def read_key(key_file: Path, *, controller_uid: int) -> bytes:
    """The key file's bytes minus the one newline an editor leaves, as the web reads it.

    The file must be the controller's own and no group or other can read it; a mounted
    secret is a link to it, which opening follows.
    """
    try:
        descriptor = os.open(key_file, os.O_RDONLY | os.O_CLOEXEC)
        try:
            status = os.fstat(descriptor)
            if not (
                stat.S_ISREG(status.st_mode)
                and status.st_uid == controller_uid
                and not status.st_mode & KEY_MODE_MASK
            ):
                raise StartRefusedError(StartRefusal.KEY)
            key = os.read(descriptor, MAXIMUM_KEY_BYTES + 1).removesuffix(b"\n")
            if not MINIMUM_KEY_LENGTH <= len(key) <= MAXIMUM_KEY_BYTES:
                raise StartRefusedError(StartRefusal.KEY)
            return key
        finally:
            os.close(descriptor)
    except OSError as error:
        raise StartRefusedError(StartRefusal.KEY) from error


def bind_listener(port: int) -> socket.socket:
    """Bind the port on every address of the pod; the packet filter admits the rest."""
    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    except OSError as error:
        raise StartRefusedError(StartRefusal.BIND) from error
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("", port))
    except OSError as error:
        listener.close()
        raise StartRefusedError(StartRefusal.BIND) from error
    return listener


def start_listening(listener: socket.socket) -> None:
    """From here on a connect is queued rather than refused."""
    try:
        listener.listen(LISTEN_BACKLOG)
    except OSError as error:
        raise StartRefusedError(StartRefusal.LISTEN) from error


def is_ready(port: int, socket_table: str) -> bool:
    """Whether the worker listens on the port and no connection is open on it.

    A worker that holds a connection serves its life, so it is not ready; the
    table is the kernel's, read without connecting, which would spend the life.
    """
    states = [
        fields[3]
        for fields in map(str.split, socket_table.splitlines()[1:])
        if int(fields[1].rpartition(":")[2], 16) == port
    ]
    return LISTENING in states and ESTABLISHED not in states


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


def serve(*, port: int, key: bytes, serving: Serving, stop_line: StopLine) -> int:
    """Bind and listen, then accept until the life ends, then write `stop_line`."""
    try:
        listener = bind_listener(port)
        with listener:
            start_listening(listener)
            logger.info("process.ready")
            accept_connections(listener, key, serving)
    except StartRefusedError as refused:
        return _refuse(refused.refusal)
    stop_line.write()
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


class PortServer(Protocol):
    """Serves the port to the peers that hold the key until the life's end."""

    def __call__(
        self, *, port: int, key: bytes, serving: Serving, stop_line: StopLine
    ) -> int:
        """Run until a refusal or the life's end."""
        ...


@dataclass(frozen=True)
class ServingOnThisMachine:
    """Serves the port with the key `read_key` reads, through `serve_port`."""

    read_key: Callable[[Path], bytes]
    serve_port: PortServer

    def __call__(
        self,
        *,
        port: int,
        key_file: Path,
        settings: WorkerSettings,
        stop_line: StopLine,
    ) -> int:
        """Read the key, then serve the port bounded by the settings."""
        try:
            key = self.read_key(key_file)
        except StartRefusedError as refused:
            return _refuse(refused.refusal)
        return self.serve_port(
            port=port, key=key, serving=real_serving(settings), stop_line=stop_line
        )


REAL_BOUNDARY = Boundary(
    _set_non_dumpable,
    _set_umask,
    partial(
        lower_process_limit,
        partial(resource.getrlimit, resource.RLIMIT_NPROC),
        partial(resource.setrlimit, resource.RLIMIT_NPROC),
    ),
    partial(fix_up_mounts, Path("/")),
    partial(
        read_snapshot, Path("/"), read_sends=partial(send_probes, program_output_as)
    ),
    SOCKET_TABLE.read_text,
    load_worker_settings,
    ServingOnThisMachine(partial(read_key, controller_uid=0), serve),
)


def _refuse(refused: Predicate | StartRefusal, **fields: str) -> int:
    logger.error(START_REFUSED_EVENT, extra={"reason": refused, **fields})
    return REFUSAL_EXIT_STATUS


def _process_limit_lowered(boundary: Boundary) -> bool:
    try:
        boundary.lower_process_limit()
    except OSError, ValueError:
        return False
    return True


def _mounts_fixed_up(boundary: Boundary) -> bool:
    try:
        boundary.fix_up_mounts()
    except OSError:
        return False
    return True


def _self_check_failure(boundary: Boundary) -> Predicate | None:
    """The predicate a start fails, in refusal order, else None."""
    if not _process_limit_lowered(boundary):
        return Predicate.RESOURCE_BOUNDS
    if not _mounts_fixed_up(boundary):
        return Predicate.MOUNTS
    return first_failing_predicate(boundary.read_snapshot())


def _ready(port: int, boundary: Boundary) -> int:
    """The readiness probe's exit status: it reads the socket table and nothing else."""
    try:
        ready = is_ready(port, boundary.read_socket_table())
    except OSError, ValueError, IndexError:
        ready = False
    return 0 if ready else NOT_READY_EXIT_STATUS


def main(argv: Sequence[str] | None = None, boundary: Boundary = REAL_BOUNDARY) -> int:
    """Run the start-up steps; exit status 2 names a refusal.

    A SIGINT or a SIGTERM anywhere ends the process in its one `process.stopped`
    line.
    """
    logline.configure(SERVICE_NAME)
    with signals_end_as_a_stop() as stop_line:
        return _start(argv, boundary, stop_line)


def _refuse_unless_given(serving_needs: dict[str, object]) -> None:
    """Refuse the start in one line naming each argument serving needs and lacks."""
    missing = [name for name, value in serving_needs.items() if value is None]
    if missing:
        refuse_argument(",".join(missing))


def _start(argv: Sequence[str] | None, boundary: Boundary, stop_line: StopLine) -> int:
    parser = argparse.ArgumentParser(description=__doc__, exit_on_error=False)
    parser.add_argument(
        "--check", action="store_true", help="exit after the self-check"
    )
    parser.add_argument(
        "--ready",
        action="store_true",
        help="exit 0 when the worker listens on --port and serves no life",
    )
    parser.add_argument(_PORT, type=int, help="the TCP port the worker serves")
    parser.add_argument(_KEY_FILE, type=Path, help="the file holding the hello key")
    arguments = read_command_line(parser, argv)
    if arguments.ready:
        if arguments.port is None:
            refuse_argument(_PORT)
        return _ready(arguments.port, boundary)
    if not arguments.check:
        _refuse_unless_given({_PORT: arguments.port, _KEY_FILE: arguments.key_file})
        logger.info("process.started")
    try:
        boundary.set_non_dumpable()
    except OSError:
        return _refuse(Predicate.NON_DUMPABLE)
    boundary.set_umask()
    failed = _self_check_failure(boundary)
    if failed is not None:
        return _refuse(failed)
    if arguments.check:
        return 0
    try:
        settings = boundary.load_settings()
    except ConfigurationError as refused:
        return _refuse(StartRefusal.SETTINGS, setting=",".join(refused.variables))
    return boundary.serve(
        port=arguments.port,
        key_file=arguments.key_file,
        settings=settings,
        stop_line=stop_line,
    )
