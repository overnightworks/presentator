"""Throwaway Redis databases on the server a test run is given.

CI's service or a developer's container is the server; every xdist worker
empties a database of its own on it, so tests stay parallel-safe.
"""

import os
import socket
import threading
from collections.abc import Generator
from contextlib import contextmanager, suppress
from urllib.parse import urlsplit

import pytest
from redis import Redis

SERVER_VARIABLE = "PRESENTATOR_TEST_REDIS_URL"
# Nothing listens here, so a client built on it fails at once; tests that never
# reach Redis name it.
NOTHING_LISTENS_HERE = "redis://127.0.0.1:1/0"
_NO_SERVER = (
    f"{SERVER_VARIABLE} names no Redis server: set it to a redis URL of a server"
    " whose databases a test may empty"
)
_UNDISTRIBUTED_WORKER = "master"
_WORKER_PREFIX = "gw"


def _server_url() -> str:
    server = os.environ.get(SERVER_VARIABLE)
    if not server:
        pytest.fail(_NO_SERVER, pytrace=False)
    return server


def _worker_index() -> int:
    worker = os.environ.get("PYTEST_XDIST_WORKER", _UNDISTRIBUTED_WORKER)
    if worker == _UNDISTRIBUTED_WORKER:
        return 0
    return int(worker.removeprefix(_WORKER_PREFIX))


def worker_database_url() -> str:
    """The URL of this worker's own database on the server."""
    return urlsplit(_server_url())._replace(path=f"/{_worker_index()}").geturl()


def emptied_database_url() -> str:
    """The URL of this worker's own database on the server, emptied."""
    url = worker_database_url()
    with opened(url) as redis:
        empty(redis)
    return url


def opened(url: str) -> Redis:
    """A client of the database `url` names, for a test to inspect it."""
    return Redis.from_url(url)  # pyright: ignore[reportUnknownMemberType] - redis-py leaves `from_url` untyped


def empty(redis: Redis) -> None:
    """Remove every key of the client's database."""
    redis.flushdb()  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped


def stored_keys(redis: Redis) -> list[str]:
    """Every key of the client's database."""
    keys = redis.keys()  # pyright: ignore[reportUnknownMemberType] - redis-py leaves its keyword arguments untyped
    return [key.decode() if isinstance(key, bytes) else key for key in keys]


class Relay:
    """A TCP hop in front of a Redis that a test can cut, as an outage does."""

    def __init__(self, target_url: str) -> None:
        """Listen on a free port and pass every connection on to `target_url`."""
        target = urlsplit(target_url)
        self._target = (target.hostname or "127.0.0.1", target.port or 6379)
        self._database = target.path
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._sockets: list[socket.socket] = [self._listener]
        self._guard = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    @property
    def url(self) -> str:
        """The URL of the hop."""
        return f"redis://127.0.0.1:{self._listener.getsockname()[1]}{self._database}"

    def sever(self) -> None:
        """Stop listening and drop every connection, so the Redis cannot be reached."""
        with self._guard:
            sockets, self._sockets = self._sockets, []
        for each in sockets:
            with suppress(OSError):
                each.shutdown(socket.SHUT_RDWR)
            each.close()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self._listener.accept()
                server = socket.create_connection(self._target)
            except OSError:
                return
            with self._guard:
                self._sockets += [client, server]
            for source, sink in ((client, server), (server, client)):
                threading.Thread(
                    target=self._pipe, args=(source, sink), daemon=True
                ).start()

    @staticmethod
    def _pipe(source: socket.socket, sink: socket.socket) -> None:
        try:
            while data := source.recv(65536):
                sink.sendall(data)
        except OSError:
            return
        finally:
            with suppress(OSError):
                sink.shutdown(socket.SHUT_RDWR)


@contextmanager
def relay_in_front_of(url: str) -> Generator[Relay]:
    """A cuttable hop in front of the Redis `url` names, cut at the end."""
    relay = Relay(url)
    try:
        yield relay
    finally:
        relay.sever()
