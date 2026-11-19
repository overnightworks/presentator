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


class _Termination(KeyboardInterrupt):
    """A SIGTERM, raised wherever it lands, as Python raises a SIGINT."""


def _raise_termination(_signal_number: int, _frame: FrameType | None) -> NoReturn:
    raise _Termination


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
    shell or a container runtime reports as 130 or 143. After the block each
    signal has its handler from before back, which a console script set to the
    silent default action before its program's imports.
    """
    stop_line = StopLine()
    handlers = {number: signal.getsignal(number) for number in _STOP_SIGNALS}
    try:
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            signal.signal(signal.SIGTERM, _raise_termination)
            yield stop_line
        finally:
            for number, handler in handlers.items():
                signal.signal(number, handler)
    except _Termination:
        _end_by(signal.SIGTERM, stop_line)
    except KeyboardInterrupt:
        _end_by(signal.SIGINT, stop_line)
