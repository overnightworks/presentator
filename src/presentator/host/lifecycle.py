"""The lines a process ends in, and the one stop line a SIGINT or a server writes.

It imports no web stack, so the sealed build worker shares it with the host.
"""

import logging
import signal
import sys
from collections.abc import Generator
from contextlib import contextmanager
from enum import StrEnum

_log = logging.getLogger(__name__)


class ServerEvent(StrEnum):
    """The lines a process ends in: a refused start, or a stop."""

    PROCESS_FAILED = "process.failed"
    PROCESS_STOPPED = "process.stopped"


class StopLine:
    """The `process.stopped` line a process ends in, written once at most.

    The server writes it once it has stopped serving; an interrupt writes it
    wherever it lands, so a stop before serving or after it ends in one line.
    """

    def __init__(self) -> None:
        """A line not written yet."""
        self._written = False

    def write(self) -> None:
        """Say the process stopped, unless it said so already."""
        if self._written:
            return
        self._written = True
        _log.info(ServerEvent.PROCESS_STOPPED)


@contextmanager
def interrupt_ends_as_a_stop() -> Generator[StopLine]:
    """End the process by SIGINT itself when one interrupts it, never as a crash.

    The block's stop line is written first, unless the server wrote it: uvicorn
    raises the signal that stopped it again once serving returns, and Python
    turns a SIGINT into a KeyboardInterrupt, whose crash line would follow the
    stop line. The default action ends the process as the signal does, as a
    SIGTERM's does, which a shell or a container runtime reports as 130.
    """
    stop_line = StopLine()
    try:
        yield stop_line
    except KeyboardInterrupt:
        stop_line.write()
        sys.stdout.flush()
        sys.stderr.flush()
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.raise_signal(signal.SIGINT)
