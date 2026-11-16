"""The accept loop's clients and its run in the test's own thread."""

import socket
import threading
from collections.abc import Callable
from pathlib import Path

from presentator.host import worker_serving
from presentator.host.worker_serving import LifeEnd, Serving
from tests.support.worker_support import (
    CLIENTS,
    KEY,
    WAIT_SECONDS,
    ScriptedLife,
    a_proven_hello,
    connected,
)


def reading_after_a_proven_hello(volume: Path) -> Callable[[], bytes]:
    """A client that proves the hello in a thread, then reads; the call is its read."""
    client = connected(volume)
    read: list[bytes] = []

    def prove_then_read() -> None:
        a_proven_hello(client)
        read.append(client.recv(1))

    thread = threading.Thread(target=prove_then_read, daemon=True)
    thread.start()
    CLIENTS.callback(thread.join)

    def what_the_client_read() -> bytes:
        thread.join(WAIT_SECONDS)
        return read[0]

    return what_the_client_read


def accept_from(
    life: ScriptedLife,
    listener: socket.socket,
    serving: Serving | None = None,
) -> LifeEnd:
    """Run the accept loop in this thread."""
    return worker_serving.accept_connections(listener, KEY, serving or life.serving())
