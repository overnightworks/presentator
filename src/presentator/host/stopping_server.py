"""The uvicorn server that marks a stop as begun the moment its signal arrives."""

import errno
import logging
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import FrameType
from typing import Final, override

import uvicorn
from starlette.types import ASGIApp

from presentator.api.health import Stopping

_log = logging.getLogger(__name__)
# uvicorn writes its server, protocol and lifespan records to this one logger.
_UVICORN_LOGGER: Final = "uvicorn.error"
# The format strings of the uvicorn records named here are constants in
# uvicorn's protocols and carry no value.
_ASGI_EXCEPTION: Final = "Exception in ASGI application\n"


class ServerEvent(StrEnum):
    """The literal events a uvicorn record is written as."""

    PROCESS_FAILED = "process.failed"
    SERVER_REFUSED = "server.refused"
    REQUEST_FAILED = "request.failed"
    SERVER_REPORTED = "server.reported"


class ServerReason(StrEnum):
    """Why the server refused a connection or failed to start: the closed set."""

    PORT_TAKEN = "port_taken"
    BIND_REFUSED = "bind_refused"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_UPGRADE = "unsupported_upgrade"


_REFUSALS: Final = {
    "Invalid HTTP request received.": ServerReason.INVALID_REQUEST,
    "Unsupported upgrade request.": ServerReason.UNSUPPORTED_UPGRADE,
}


@dataclass(frozen=True, slots=True)
class _NamedRecord:
    """What a uvicorn record is written as: its event, its codes and its level."""

    event: ServerEvent
    codes: Mapping[str, str] = field(default_factory=dict[str, str])
    level: int | None = None


class _UvicornRecordsAsEvents(logging.Filter):
    """Write each uvicorn record as a literal event with codes, never its text.

    uvicorn's messages quote the bound address and its tracebacks name paths;
    a record this filter does not know keeps only its level and logger.

    It is a stopgap until logline#41 ships, which names these records in
    logline's own configure; it is removed then. Its risk: it matches
    uvicorn's message constants, so a uvicorn release that rewords one turns
    that record into `server.reported`, which the tests of these events show.
    """

    @override
    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        error = record.exc_info[1] if record.exc_info else None
        named = _named(record.msg, error)
        # A fresh record carries none of the original's text, arguments,
        # traceback or uvicorn's coloured copy of the message.
        renamed = logging.LogRecord(
            record.name,
            record.levelno if named.level is None else named.level,
            record.pathname,
            record.lineno,
            named.event,
            None,
            None,
            record.funcName,
        )
        renamed.__dict__.update(named.codes)
        return renamed


def _named(message: object, error: BaseException | None) -> _NamedRecord:
    """The event a uvicorn record with `message` and `error` is written as."""
    if isinstance(message, OSError):
        # The one OSError uvicorn logs is its failure to bind the listening socket.
        reason = (
            ServerReason.PORT_TAKEN
            if message.errno == errno.EADDRINUSE
            else ServerReason.BIND_REFUSED
        )
        return _NamedRecord(
            ServerEvent.PROCESS_FAILED,
            {"reason": reason, "error_type": type(message).__name__},
        )
    refused = _REFUSALS.get(str(message))
    if refused is not None:
        # A refusal is info; uvicorn writes it as a warning.
        return _NamedRecord(
            ServerEvent.SERVER_REFUSED, {"reason": refused}, level=logging.INFO
        )
    event = (
        ServerEvent.REQUEST_FAILED
        if message == _ASGI_EXCEPTION
        else ServerEvent.SERVER_REPORTED
    )
    if error is None:
        return _NamedRecord(event)
    return _NamedRecord(event, {"error_type": type(error).__name__})


# One instance: Logger.addFilter ignores an instance it already holds.
_UVICORN_RECORDS_AS_EVENTS: Final = _UvicornRecordsAsEvents()


def name_uvicorn_records() -> None:
    """Write uvicorn's warnings and errors as literal events; call after `configure`."""
    logging.getLogger(_UVICORN_LOGGER).addFilter(_UVICORN_RECORDS_AS_EVENTS)


class StoppingServer(uvicorn.Server):
    """A server whose signal marks the stop as begun before uvicorn starts it.

    uvicorn closes each open WebSocket with 1012 and lets a running answer
    finish; the mark makes `/health` unready and refuses new asks and hearings
    from the signal on. It writes `process.ready` once its socket listens and
    `process.stopped` once it has stopped, so a start that cannot bind writes
    no ready line.
    """

    def __init__(self, config: uvicorn.Config, stopping: Stopping) -> None:
        """Serve `config`, marking `stopping` when a signal ends the server."""
        super().__init__(config)
        self._stopping = stopping

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        """Start serving, then say the process is ready."""
        await super().startup(sockets)
        _log.info("process.ready")

    @override
    async def shutdown(self, sockets: list[socket.socket] | None = None) -> None:
        """Stop serving, then say the process stopped.

        uvicorn re-raises the signal that stopped it once serving returns, so the
        process ends before any line after `serve`.
        """
        await super().shutdown(sockets)
        _log.info("process.stopped")

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
        # logline's configure owns uvicorn's loggers, the request line
        # replaces its access log, and the rejected WebSocket handshake
        # records that carry a path stay out of the log. uvicorn's own
        # info lines are free text, so only its warnings reach the log, as
        # the events `name_uvicorn_records` makes of them.
        log_config=None,
        log_level=logging.WARNING,
    )
