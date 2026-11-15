"""Show that a worker pod drops what reaches it on a port other than its own.

Usage: `python -I - listen` and `python -I - reach HOST:PORT`, the program on stdin.

`listen` runs in the worker's container as root. It opens a listener on every
address, prints its port as one JSON line and keeps listening in a process of its
own for a while after it returns, so a connection that reaches the pod would
complete. `reach` runs in a web pod: it opens one new TCP connection to the
address and prints one JSON line naming how it ended, `connected`, `timeout` or
the errno's name.
"""

import errno
import json
import os
import socket
import sys
import time

LISTEN_SECONDS = 60
REACH_SECONDS = 5.0
LISTENING = "listen"


def listen() -> None:
    """Print the port of a listener that outlives this program."""
    listener = socket.socket()
    listener.bind(("", 0))
    listener.listen()
    sys.stdout.write(json.dumps({"port": listener.getsockname()[1]}) + "\n")
    sys.stdout.flush()
    if os.fork():
        return
    os.setsid()
    nowhere = os.open(os.devnull, os.O_RDWR)
    for descriptor in range(3):
        os.dup2(nowhere, descriptor)
    time.sleep(LISTEN_SECONDS)
    os._exit(0)


def reach(target: str) -> str:
    """How a new TCP connection to HOST:PORT ended."""
    host, _, port = target.rpartition(":")
    with socket.socket() as probe:
        probe.settimeout(REACH_SECONDS)
        try:
            probe.connect((host, int(port)))
        except TimeoutError:
            return "timeout"
        except OSError as refused:
            return errno.errorcode.get(refused.errno or 0, type(refused).__name__)
    return "connected"


def main(arguments: list[str]) -> None:
    """Run the one mode the arguments name."""
    if arguments == [LISTENING]:
        listen()
        return
    (_, target) = arguments
    sys.stdout.write(json.dumps({"end": reach(target)}) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
