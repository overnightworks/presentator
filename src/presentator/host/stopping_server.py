"""The uvicorn server that marks a stop as begun the moment its signal arrives.

It also owns the process's JSON log lines.
"""

import logging
import os
import socket
from collections.abc import Awaitable, Callable
from types import FrameType
from typing import Final, override

import logline
import uvicorn
from starlette.types import ASGIApp

from presentator.api.health import Stopping
from presentator.host.lifecycle import ServerReason, StopLine, end_failed

_log = logging.getLogger(__name__)
# The variable logline reads the root log level from.
_LOG_LEVEL: Final = "LOG_LEVEL"


def configure_logging(service: str) -> None:
    """Make every line of this process one JSON object, uvicorn's included.

    A `LOG_LEVEL` that logline does not name refuses the start, in the one
    `process.failed` line written at logline's default level.
    """
    try:
        logline.configure(service)
    except ValueError:
        # logline refuses an unknown level before it changes anything, and its
        # message quotes the value; the process ends right after this line.
        del os.environ[_LOG_LEVEL]
        logline.configure(service)
        end_failed(ServerReason.CONFIGURATION_REFUSED, setting=_LOG_LEVEL)


class StoppingServer(uvicorn.Server):
    """A server whose signal marks the stop as begun before uvicorn starts it.

    uvicorn closes each open WebSocket with 1012 and lets a running answer
    finish, once the talks that run a decision have closed; the mark makes
    `/health` unready and refuses new asks and hearings from the signal on. It
    writes `process.ready` once its socket listens and `process.stopped` once it
    has stopped, so a start that cannot bind writes no ready line.
    """

    def __init__(
        self,
        config: uvicorn.Config,
        stopping: Stopping,
        stop_line: StopLine,
        talks_closed: Callable[[], Awaitable[None]],
    ) -> None:
        """Serve `config`, marking `stopping` when a signal ends the server.

        Once the open talks are closed and it has stopped, it writes `stop_line`.
        `talks_closed` returns once every talk that runs a decision has closed.
        """
        super().__init__(config)
        self._stopping = stopping
        self._stop_line = stop_line
        self._talks_closed = talks_closed

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start serving, then say the process is ready."""
        await super().startup(sockets)
        _log.info("process.ready")

    @override
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """Wait for the open talks, stop serving, then say the process stopped.

        The wait comes first because uvicorn's first act is to close every
        WebSocket with 1012, which would cut a decision still running.
        uvicorn ends the app's lifespan inside this stop, so the line follows
        the chores' end and the instance's close. uvicorn re-raises the signal
        that stopped it once serving returns, so the process ends before any
        line after `serve`.
        """
        await self._talks_closed()
        await super().shutdown(sockets)
        self._stop_line.write()

    @override
    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        """Mark the stop as begun, then let uvicorn start it."""
        self._stopping.begin()
        super().handle_exit(sig, frame)


def served_config(app: ASGIApp, *, host: str, port: int) -> uvicorn.Config:
    """How uvicorn serves `app` on `host` and `port`."""
    return uvicorn.Config(
        app,
        host=host,
        port=port,
        proxy_headers=False,
        ws="wsproto",
        # logline's configure owns uvicorn's loggers and their level: it writes
        # uvicorn's records as literal events, drops those that carry a path,
        # and the request line replaces the access log.
        log_config=None,
    )
