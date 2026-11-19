"""The lines a process ends in, and the one stop line a stop signal or a server writes.

It imports no web stack, so the sealed build worker shares it with the host.
"""

import logging
import signal
import sys
from collections.abc import Generator
from contextlib import contextmanager
from enum import StrEnum
from types import FrameType
from typing import Final, NoReturn

_log = logging.getLogger(__name__)
_STOP_SIGNALS: Final = (signal.SIGINT, signal.SIGTERM)
# A shell's and a container runtime's exit status of a process a signal ended.
_SIGNALLED_EXIT_BASE: Final = 128


class ServerEvent(StrEnum):
    """The lines a process ends in: a refused start, or a stop."""

    PROCESS_FAILED = "process.failed"
    PROCESS_STOPPED = "process.stopped"


class StopLine:
    """The `process.stopped` line a process ends in, written once at most.

    The server writes it once it has stopped serving; a stop signal writes it
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


def _end_by(stop_signal: signal.Signals, stop_line: StopLine) -> NoReturn:
    """Write the stop line, then end the process as `stop_signal` itself does.

    The kernel ignores a signal at its default action in a PID 1, as the build
    worker is in its container; that process ends in the status a shell or a
    container runtime gives a process the signal ended.
    """
    stop_line.write()
    sys.stdout.flush()
    sys.stderr.flush()
    signal.signal(stop_signal, signal.SIG_DFL)
    signal.raise_signal(stop_signal)
    raise SystemExit(_SIGNALLED_EXIT_BASE + stop_signal)


@contextmanager
def signals_end_as_a_stop() -> Generator[StopLine]:
    """End the process by the SIGINT or SIGTERM that stops it, never as a crash.

    The block's stop line is written first, unless the server wrote it: uvicorn
    raises the signal that stopped it again once serving returns, and Python
    turns a SIGINT into a KeyboardInterrupt, whose crash line would follow the
    stop line. The default action ends the process as the signal does, which a
    shell or a container runtime reports as 130 or 143. A signal the parent
    process ignores stays ignored. After the block each
    signal has its handler from before back, which a console script set to the
    silent default action before its program's imports.
    """
    stop_line = StopLine()
    stopped_by = signal.SIGINT

    def interrupt(signal_number: int, _frame: FrameType | None) -> NoReturn:
        """Raise either stop signal wherever it lands, as Python raises a SIGINT."""
        nonlocal stopped_by
        stopped_by = signal.Signals(signal_number)
        raise KeyboardInterrupt

    handlers = {number: signal.getsignal(number) for number in _STOP_SIGNALS}
    try:
        try:
            for number, handler in handlers.items():
                if handler != signal.SIG_IGN:
                    signal.signal(number, interrupt)
            yield stop_line
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
    except KeyboardInterrupt:
        _end_by(stopped_by, stop_line)
