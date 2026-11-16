"""The uvicorn server that marks a stop as begun the moment its signal arrives."""

import logging
from types import FrameType

import uvicorn
from starlette.types import ASGIApp

from presentator.api.health import Stopping


class StoppingServer(uvicorn.Server):
    """A server whose signal marks the stop as begun before uvicorn starts it.

    uvicorn closes each open WebSocket with 1012 and lets a running answer
    finish; the mark makes `/health` unready and refuses new asks and hearings
    from the signal on.
    """

    def __init__(self, config: uvicorn.Config, stopping: Stopping) -> None:
        """Serve `config`, marking `stopping` when a signal ends the server."""
        super().__init__(config)
        self._stopping = stopping

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
        # info lines are free text, so only its warnings reach the log.
        log_config=None,
        log_level=logging.WARNING,
    )
