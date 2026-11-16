"""The uvicorn server that marks a stop as begun the moment its signal arrives."""

from types import FrameType

import uvicorn

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
