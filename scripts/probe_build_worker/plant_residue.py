"""Plant one residue, then become the worker (`python -I - RESIDUE UID:GID COMMAND...`).

The source arrives on stdin. The residue is planted as root or as the build
identity, and the command replaces this process, so the worker is PID 1 of the
container. A plant that runs as the build identity is reaped and exits 1 when
it fails. The `process` child stays alive on purpose; the parent waits until it
has dropped to the identity and exits 1 when it could not.
"""

import ctypes
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path

IPC_CREAT = 0o1000
OWNER_ONLY = 0o600
SHARED_MEMORY_KEY = 0x5331
MESSAGE_QUEUE_KEY = 0x5332
SEMAPHORE_KEY = 0x5333
SEGMENT_BYTES = 4096
libc = ctypes.CDLL(None, use_errno=True)


def checked(result: int) -> int:
    """Return a libc result, raising the errno it set on failure."""
    if result < 0:
        raise OSError(ctypes.get_errno(), "libc call failed")
    return result


def become(identity: tuple[int, int]) -> None:
    """Drop to the identity, for the child that plants."""
    uid, gid = identity
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)


def as_build_user(identity: tuple[int, int], plant: Callable[[], object]) -> None:
    """Plant in a child of the identity and reap it; a failed plant exits 1."""
    child = os.fork()
    if child == 0:
        try:
            become(identity)
            plant()
        except BaseException as error:  # noqa: BLE001 - the child reports and ends either way
            sys.stderr.write(f"plant failed: {error!r}\n")
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(child, 0)
    if os.waitstatus_to_exitcode(status):
        sys.exit(1)


def leave_a_sleeping_child(identity: tuple[int, int]) -> None:
    """Fork a child of the identity that outlives the exec; exit 1 unless it dropped."""
    ready_read, ready_write = os.pipe()
    if os.fork() == 0:
        try:
            os.close(ready_read)
            become(identity)
            os.write(ready_write, b"1")
            os.close(ready_write)
            signal.pause()
        except BaseException as error:  # noqa: BLE001 - the child reports and ends either way
            sys.stderr.write(f"plant failed: {error!r}\n")
        finally:
            os._exit(1)
    os.close(ready_write)
    dropped = os.read(ready_read, 1)
    os.close(ready_read)
    if not dropped:
        sys.exit(1)


def closed_directory() -> None:
    """A directory holding a file, closed to everyone."""
    closed = Path("/tmp/closed")  # noqa: S108 - the scratch place of the container this runs in
    closed.mkdir()
    (closed / "file").write_text("residue")
    closed.chmod(0)


def message_queue() -> None:
    """A POSIX message queue the build user owns."""
    libc.mq_open.argtypes = [
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    checked(libc.mq_open(b"/residue", os.O_CREAT | os.O_RDWR, OWNER_ONLY, None))


def plant(residue: str, identity: tuple[int, int]) -> None:
    """Plant the named residue."""
    match residue:
        case "nothing":
            pass
        case "process":
            leave_a_sleeping_child(identity)
        case "work-entry":
            Path("/work/deck").mkdir()
        case "closed-directory":
            as_build_user(identity, closed_directory)
        case "shared-memory-file":
            as_build_user(
                identity,
                lambda: Path("/dev/shm/residue").write_text("residue"),  # noqa: S108 - the scratch place of the container this runs in
            )
        case "message-queue":
            as_build_user(identity, message_queue)
        case "sysv-shm":
            as_build_user(
                identity,
                lambda: checked(
                    libc.shmget(
                        SHARED_MEMORY_KEY, SEGMENT_BYTES, IPC_CREAT | OWNER_ONLY
                    )
                ),
            )
        case "sysv-msg":
            as_build_user(
                identity,
                lambda: checked(libc.msgget(MESSAGE_QUEUE_KEY, IPC_CREAT | OWNER_ONLY)),
            )
        case "sysv-sem":
            as_build_user(
                identity,
                lambda: checked(libc.semget(SEMAPHORE_KEY, 1, IPC_CREAT | OWNER_ONLY)),
            )
        case _:
            sys.exit(1)


residue, owner, *command = sys.argv[1:]
uid, gid = owner.split(":")
plant(residue, (int(uid), int(gid)))
os.execvp(command[0], command)  # noqa: S606 - the command is the worker service's own
