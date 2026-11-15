"""Show what a deck's code reaches from inside a worker pod: only the loopback.

Usage: `python -I - HOST:PORT...`, the program on stdin.

Run inside the worker's container as root, which keeps the capabilities to
change user. For the build user and for the wire user it forks, drops to that
user and tries one new TCP connection to the loopback address of a listener
this program opened and sends one datagram to each HOST:PORT, then prints one
JSON line naming how every attempt ended: `connected` or `sent`, or the errno's
name. The pod's packet filter drops a new socket's packets off the loopback in
its output hook, which refuses a datagram's send with EPERM at the source, where
a dropped TCP connection only times out and so does not tell the filter from a
drop at the target.
"""

import errno
import json
import os
import socket
import sys
from collections.abc import Callable
from functools import partial

from presentator.host.worker_self_check import BUILD_IDENTITY, WIRE_IDENTITY

SECONDS = 3.0
LOOPBACK = "127.0.0.1"
USERS = {"build": BUILD_IDENTITY, "wire": WIRE_IDENTITY}
FIRST_ATTEMPT = "loopback"


def connect(address: tuple[str, int]) -> str:
    """How a new TCP connection to the address ended."""
    with socket.socket() as probe:
        probe.settimeout(SECONDS)
        try:
            probe.connect(address)
        except OSError as refused:
            return errno.errorcode.get(refused.errno or 0, type(refused).__name__)
    return "connected"


def send_datagram(address: tuple[str, int]) -> str:
    """How sending one datagram to the address ended."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.sendto(b"probe", address)
        except OSError as refused:
            return errno.errorcode.get(refused.errno or 0, type(refused).__name__)
    return "sent"


def as_user(
    identity: tuple[int, int], attempts: dict[str, Callable[[], str]]
) -> dict[str, str]:
    """The attempts' ends, as a child that dropped to the identity, read over a pipe."""
    reader, writer = os.pipe()
    if os.fork() == 0:
        os.close(reader)
        os.setgroups([])
        uid, gid = identity
        os.setgid(gid)
        os.setuid(uid)
        ends = {name: attempt() for name, attempt in attempts.items()}
        os.write(writer, json.dumps(ends).encode())
        os._exit(0)
    os.close(writer)
    with os.fdopen(reader) as pipe:
        said = pipe.read()
    os.wait()
    return json.loads(said)


def main(targets: list[str]) -> None:
    """Open the listener, run both users' attempts and print their ends."""
    with socket.socket() as listener:
        listener.bind((LOOPBACK, 0))
        listener.listen()
        attempts: dict[str, Callable[[], str]] = {
            FIRST_ATTEMPT: partial(connect, (LOOPBACK, listener.getsockname()[1]))
        }
        for target in targets:
            host, _, port = target.rpartition(":")
            attempts[target] = partial(send_datagram, (host, int(port)))
        said = {name: as_user(who, attempts) for name, who in USERS.items()}
    sys.stdout.write(json.dumps(said) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
