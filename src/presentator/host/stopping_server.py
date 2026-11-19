"""The uvicorn server that marks a stop as begun the moment its signal arrives.

It also owns the process's JSON log lines and the one line a failed start
ends in.
"""

import logging
import os
import socket
from enum import StrEnum
from types import FrameType
from typing import Final, NoReturn, override

import logline
import uvicorn
from starlette.types import ASGIApp

from presentator.adapters.postgres.migrate import RefusalReason
from presentator.api.health import Stopping
from presentator.host.lifecycle import ServerEvent, StopLine

_log = logging.getLogger(__name__)
# The variable logline reads the root log level from.
_LOG_LEVEL: Final = "LOG_LEVEL"


class ServerReason(StrEnum):
    """Why the process failed to start.

    Together with the database's `RefusalReason`, the closed set of reasons.
    """

    CONFIGURATION_REFUSED = "configuration_refused"


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


def end_failed(reason: RefusalReason | ServerReason, **codes: str) -> NoReturn:
    """End the process in one line naming the refusal, without its traceback.

    The traceback would name the database's host and port or a file's path, so
    the line carries only codes, such as the class name of the driver's error.
    """
    _log.error(ServerEvent.PROCESS_FAILED, extra={"reason": reason, **codes})
    raise SystemExit(1) from None


class StoppingServer(uvicorn.Server):
    """A server whose signal marks the stop as begun before uvicorn starts it.

    uvicorn closes each open WebSocket with 1012 and lets a running answer
    finish; the mark makes `/health` unready and refuses new asks and hearings
    from the signal on. It writes `process.ready` once its socket listens and
    `process.stopped` once it has stopped, so a start that cannot bind writes
    no ready line.
    """

    def __init__(
        self, config: uvicorn.Config, stopping: Stopping, stop_line: StopLine
    ) -> None:
        """Serve `config`, marking `stopping` when a signal ends the server.

        Once it has stopped, it writes `stop_line`.
        """
        super().__init__(config)
        self._stopping = stopping
        self._stop_line = stop_line

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start serving, then say the process is ready."""
        await super().startup(sockets)
        _log.info("process.ready")

    @override
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """Stop serving, then say the process stopped.

        uvicorn ends the app's lifespan inside this stop, so the line follows
        the chores' end and the instance's close. uvicorn re-raises the signal
        that stopped it once serving returns, so the process ends before any
        line after `serve`.
        """
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
