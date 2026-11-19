"""The web's client of the build workers: one worker life per step, over TCP."""

import logging
import os
import shutil
import socket
import time
from collections.abc import Buffer, Callable
from contextlib import closing
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Final

from presentator.adapters.build_wire import (
    MAXIMUM_ENTRIES,
    TOOLCHAIN_SCRATCH_NAME,
    BuiltOutcome,
    HelloRefusedError,
    RefusalLimit,
    RefusedOutcome,
    StepHeader,
    TreeRefusedError,
    WireRefusedError,
    answer_hello,
    check_step_tree,
    check_upload,
    read_empty_end,
    read_outcome,
    read_status,
    receive_entries,
    send_step,
    send_tree,
)
from presentator.adapters.builds import BuildPlace, BuildStep, stopped_by
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    TALK_DIRECTORY,
    BuildFailure,
    BuildFailureReason,
    StepKind,
    ToolchainUnavailable,
)

CONNECT_WINDOW_SECONDS: Final = 70.0
ATTEMPT_SECONDS: Final = 2.0
FIRST_RETRY_SECONDS: Final = 0.25
LONGEST_RETRY_SECONDS: Final = 4.0
REPLY_MARGIN_SECONDS: Final = 5.0
STAGED_PREFIX: Final = "staged-"
STAGING_MODE: Final = 0o700

_STAGING_EVENT: Final = "build_staging_removal.refused"
_STAGING_REASON: Final = "staging_stayed"
_ATTEMPT_EVENT: Final = "worker_attempt.failed"

_log = logging.getLogger(__name__)


class Fault(StrEnum):
    """The class of a fault of one exchange: the closed set of its `reason` codes."""

    NOT_THE_WORKER = "not_the_worker"
    DEADLINE = "deadline"
    CONNECTION = "connection"
    REPLY_GRAMMAR = "reply_grammar"
    STEP_CONTRACT = "step_contract"
    LOCAL = "local"


class _NotYetError(Exception):
    """The worker is not there yet; the attempt is retried within the window."""


def fault_of(error: Exception) -> Fault:
    """The class of fault an exchange's exception is."""
    if isinstance(error, HelloRefusedError):
        return Fault.NOT_THE_WORKER
    if isinstance(error, TreeRefusedError):
        return Fault.STEP_CONTRACT
    if isinstance(error, TimeoutError):
        return Fault.DEADLINE
    if isinstance(error, ConnectionError):
        return Fault.CONNECTION
    if isinstance(error, WireRefusedError):
        return Fault.REPLY_GRAMMAR
    return Fault.LOCAL


class _DeadlineSocket(socket.socket):
    """A socket whose calls together wait only until one deadline.

    A timeout set once bounds each call, not their sum, so a reply dripped a
    byte at a time would outlive it; every call here is armed with what is left
    by the clock the deadline is read on.
    """

    deadline = float("inf")

    def __init__(self, family: int, clock: Callable[[], float]) -> None:
        """A stream socket of that family whose deadlines `clock` reads."""
        super().__init__(family, socket.SOCK_STREAM)
        self._clock = clock

    def until(self, deadline: float) -> None:
        """Bound every later call by this time of the clock."""
        self.deadline = deadline

    def _armed(self) -> None:
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError
        self.settimeout(remaining)

    def connect(self, address: str | Buffer | tuple[object, ...] | int) -> None:
        """Connect within the deadline."""
        self._armed()
        super().connect(address)

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        """Receive within the deadline."""
        self._armed()
        return super().recv(bufsize, flags)

    def sendall(self, data: Buffer, flags: int = 0) -> None:
        """Send everything within the deadline."""
        self._armed()
        super().sendall(data, flags)


def _begun_to_answer(connection: socket.socket) -> None:
    """Peek the first byte of the hello; a close before it means no worker is ready."""
    if not connection.recv(1, socket.MSG_PEEK):
        raise _NotYetError


def _nothing_follows(connection: socket.socket) -> None:
    if connection.recv(1):
        raise WireRefusedError


def _refusal(outcome: RefusedOutcome, megabytes: int) -> BuildFailure:
    """The failure of a refused reply; a written count off its limit is a fault."""
    if (outcome.written is not None) != (outcome.limit is RefusalLimit.MEGABYTES):
        raise WireRefusedError
    if outcome.limit is RefusalLimit.MEGABYTES:
        return BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=outcome.written,
            allowed_megabytes=megabytes,
        )
    if outcome.limit is RefusalLimit.ENTRIES:
        return BuildFailure(
            reason=BuildFailureReason.OUTPUT_ENTRIES, allowed_entries=MAXIMUM_ENTRIES
        )
    return BuildFailure(reason=BuildFailureReason.OUTPUT_SHAPE)


def _admitted(staged: Path, place: BuildPlace, kind: StepKind) -> None:
    """Move the step's one admitted entry into out/, which must not hold it yet."""
    name = TALK_DIRECTORY if kind is StepKind.BUILD else PDF_FILE
    target = place.here / OUTPUT_DIRECTORY / name
    if os.path.lexists(target):
        raise FileExistsError(target)
    (staged / name).rename(target)


def _staged_reply(
    connection: socket.socket, staged: Path, step: BuildStep, megabytes: int
) -> None:
    """Receive a built reply into the staging, hold it to the contract, admit it."""
    descriptor = os.open(staged, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        receive_entries(connection, descriptor, megabytes)
        _nothing_follows(connection)
        check_step_tree(descriptor, step.name, megabytes)
    finally:
        os.close(descriptor)
    _admitted(staged, step.place, step.name)


def _answer(
    connection: socket.socket, staged: Path, step: BuildStep, megabytes: int
) -> BuildFailure | None:
    outcome = read_outcome(connection)
    if isinstance(outcome, BuiltOutcome):
        _staged_reply(connection, staged, step, megabytes)
        return None
    failure = (
        _refusal(outcome, megabytes)
        if isinstance(outcome, RefusedOutcome)
        else BuildFailure.said(outcome.text)
    )
    read_empty_end(connection)
    _nothing_follows(connection)
    stopped_by(step.line, failure)
    return failure


def _refused_upload(
    refusal: TreeRefusedError, step: BuildStep, megabytes: int
) -> BuildFailure:
    """The failure of a deck the upload check refuses, noted on the step's line."""
    if refusal.outcome.limit is RefusalLimit.SHAPE:
        failure = BuildFailure(reason=BuildFailureReason.DECK_PATHS)
    else:
        failure = BuildFailure(
            reason=BuildFailureReason.DECK_MEGABYTES,
            allowed_megabytes=megabytes,
            allowed_entries=MAXIMUM_ENTRIES,
        )
    stopped_by(step.line, failure)
    return failure


def _failure_of(error: Exception, step: BuildStep) -> BuildFailure:
    step.line.fail(fault_of(error))
    return BuildFailure.without_words()


def _cleared(staged: Path, remove_staging: Callable[[Path], None]) -> None:
    try:
        remove_staging(staged)
    except OSError as error:
        _log.warning(
            _STAGING_EVENT,
            extra={"reason": _STAGING_REASON, "error_type": type(error).__name__},
        )


def exchange_with_worker(
    connection: socket.socket,
    *,
    step: BuildStep,
    deck: int,
    megabytes: int,
    remove_staging: Callable[[Path], None],
) -> BuildFailure | None:
    """Upload the deck and take the worker's reply for a step it has answered.

    Every fault of the exchange comes back as a BuildFailure whose reason is on
    the step's one line, and the staging is gone afterwards; nothing but the
    step's one admitted entry reaches out/.
    """
    staged = step.place.here / f"{STAGED_PREFIX}{step.name}"
    try:
        staged.mkdir(mode=STAGING_MODE)
        try:
            send_tree(connection, deck, omitting=TOOLCHAIN_SCRATCH_NAME)
            return _answer(connection, staged, step, megabytes)
        finally:
            _cleared(staged, remove_staging)
    except (OSError, WireRefusedError, TreeRefusedError) as error:
        return _failure_of(error, step)


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkerToolchain:
    """Runs a step by asking a build worker, which owns the sealed container.

    One worker life serves one step: the web proves the key in the hello,
    sends the step, uploads the deck and takes the output back into out/.
    Before the worker's STATUS everything waits only until the connect window
    ends; afterwards only until the worker's own last deadline plus a margin
    has passed. The address is a Service, so each step lands on a worker that
    is ready.
    """

    host: str
    port: int
    key: bytes = field(repr=False)
    megabytes: int
    reply_bound_seconds: float
    connect_window_seconds: float = CONNECT_WINDOW_SECONDS
    attempt_seconds: float = ATTEMPT_SECONDS
    first_retry_seconds: float = FIRST_RETRY_SECONDS
    longest_retry_seconds: float = LONGEST_RETRY_SECONDS
    reply_margin_seconds: float = REPLY_MARGIN_SECONDS
    remove_staging: Callable[[Path], None] = shutil.rmtree
    clock: Callable[[], float] = time.monotonic

    def ran(self, step: BuildStep) -> BuildFailure | ToolchainUnavailable | None:
        """Send the deck to the worker for that step and admit what it built.

        A fault before the worker's STATUS says nothing about the deck, whose
        step header the web has already checked: the step is unavailable, not
        failed.
        """
        try:
            deck = os.open(
                step.place.here / DECK_DIRECTORY,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except OSError as error:
            return _failure_of(error, step)
        try:
            return self._refused_deck(deck, step) or self._through_worker(deck, step)
        finally:
            os.close(deck)

    def _refused_deck(self, deck: int, step: BuildStep) -> BuildFailure | None:
        try:
            check_upload(deck, self.megabytes)
        except TreeRefusedError as refusal:
            return _refused_upload(refusal, step, self.megabytes)
        except OSError as error:
            return _failure_of(error, step)
        return None

    def _through_worker(
        self, deck: int, step: BuildStep
    ) -> BuildFailure | ToolchainUnavailable | None:
        window_ends = self.clock() + self.connect_window_seconds
        header = StepHeader(step=step.name, slug=step.slug)
        try:
            connection = self._at_status(header, window_ends)
        except (OSError, WireRefusedError) as error:
            step.line.fail(fault_of(error))
            return ToolchainUnavailable()
        with closing(connection):
            connection.until(
                window_ends + self.reply_bound_seconds + self.reply_margin_seconds
            )
            return exchange_with_worker(
                connection,
                step=step,
                deck=deck,
                megabytes=self.megabytes,
                remove_staging=self.remove_staging,
            )

    def _at_status(self, header: StepHeader, window_ends: float) -> _DeadlineSocket:
        """A connection whose hello is proven and whose STATUS has been read."""
        connection = self._opened(window_ends)
        try:
            send_step(connection, header)
            read_status(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    def _opened(self, window_ends: float) -> _DeadlineSocket:
        """A connection to a worker that has begun to answer and proved the key.

        Retried within the window while no worker is there yet, or the one the
        Service chose is busy or restarting and closes or stays silent before
        it speaks; a worker that speaks and does not prove the key is never
        retried. Each attempt waits only `attempt_seconds` for the first byte, as
        the Service may still route to a pod that has ended and answers nothing.
        """
        wait = self.first_retry_seconds
        while True:
            try:
                return self._attempt(window_ends)
            except _NotYetError as error:
                _log.debug(
                    _ATTEMPT_EVENT,
                    extra={"error_type": type(error.__cause__ or error).__name__},
                )
                remaining = window_ends - self.clock()
                time.sleep(max(0.0, min(wait, remaining)))
                wait = min(2 * wait, self.longest_retry_seconds)

    def _attempt(self, window_ends: float) -> _DeadlineSocket:
        family, _type, _protocol, _name, address = socket.getaddrinfo(
            self.host, self.port, type=socket.SOCK_STREAM
        )[0]
        connection = _DeadlineSocket(family, self.clock)
        connection.until(min(window_ends, self.clock() + self.attempt_seconds))
        try:
            connection.connect(address)
            _begun_to_answer(connection)
        except (
            ConnectionRefusedError,
            ConnectionResetError,
            BrokenPipeError,
        ) as error:
            connection.close()
            raise _NotYetError from error
        except TimeoutError as error:
            connection.close()
            if self.clock() >= window_ends:
                raise
            raise _NotYetError from error
        except BaseException:
            connection.close()
            raise
        connection.until(window_ends)
        try:
            answer_hello(connection, self.key)
        except BaseException:
            connection.close()
            raise
        return connection
