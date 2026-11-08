"""One owned peer that serves an app on a socket the test binds and stops with it."""

from __future__ import annotations

import asyncio
import socket
from contextlib import suppress
from typing import TYPE_CHECKING, override

import uvicorn
from wsproto.utilities import LocalProtocolError

if TYPE_CHECKING:
    from fastapi import FastAPI


def loopback_listener() -> socket.socket:
    """Bind a loopback TCP socket on a port the system picks."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    return listener


def origin_of(listener: socket.socket) -> str:
    """The origin a client reaches this listener at."""
    host, port = listener.getsockname()
    return f"http://{host}:{port}"


class _AnnouncingServer(uvicorn.Server):
    """A uvicorn server that says when it has started, so a stop never beats it."""

    started_up: asyncio.Event

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.started_up.set()


class Serving:
    """Serve ``app`` on a bound ``listener`` for the length of an ``async with``."""

    def __init__(self, app: FastAPI, listener: socket.socket) -> None:
        """Hold the app and the socket it will serve on."""
        self._app = app
        self._listener = listener
        self._server: _AnnouncingServer | None = None
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> None:
        """Start serving, and return once the socket is accepting."""
        self._listener.listen()
        self._listener.settimeout(0.0)
        server = _AnnouncingServer(
            uvicorn.Config(self._app, log_level="error", proxy_headers=False),
        )
        server.started_up = asyncio.Event()
        self._server = server
        self._task = asyncio.create_task(server.serve(sockets=[self._listener]))
        await asyncio.wait(
            [self._task, asyncio.create_task(server.started_up.wait())],
            return_when=asyncio.FIRST_COMPLETED,
        )

    async def __aexit__(self, *_error: object) -> None:
        """Stop serving."""
        assert self._server is not None
        assert self._task is not None
        # Uvicorn closes a connection whose peer already left, which wsproto refuses.
        with suppress(LocalProtocolError):
            await self._server.shutdown(sockets=[self._listener])
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
