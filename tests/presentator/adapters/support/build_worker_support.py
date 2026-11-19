"""What the build worker client's test modules share."""

import logging
import os
import socket
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path

from logline import StepTimer

from presentator.adapters.build_wire import (
    FailedOutcome,
    RefusalLimit,
    RefusedOutcome,
    StepHeader,
    WireRefusedError,
    read_request,
    receive_entries,
    send_status,
    verify_hello,
)
from presentator.adapters.build_worker import STAGED_PREFIX, WorkerToolchain
from presentator.adapters.builds import BuildPlace, BuildStep
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    BuildFailure,
    StepKind,
)
from tests.support import worker_support as support
from tests.support.log_lines import LoggedText, LogLine
from tests.support.worker_support import KEY, LOCAL_ADDRESS, port_of

MEGABYTES = 1
WINDOW = 0.6
HELLO_SECONDS = 1.0
WITHOUT_A_REASON = BuildFailure.without_words()
INDEX = b"<html>"
PDF = b"%PDF"
BUILT_TALK = support.OUTCOME_BUILT + support.talk_entries(INDEX)
BUILT_PDF = (
    support.OUTCOME_BUILT
    + support.file_entry(PDF_FILE.encode(), PDF)
    + support.end_entry(1, len(PDF))
)
BUILT_BY_STEP = {StepKind.BUILD: BUILT_TALK, StepKind.EXPORT: BUILT_PDF}


def refused_reply(limit: RefusalLimit, written: int | None = None) -> bytes:
    header = RefusedOutcome(limit=limit, written=written).model_dump_json().encode()
    return support.framed(header, b"PBW\x01\x05") + support.END


def failed_reply(text: str | None) -> bytes:
    header = FailedOutcome(text=text).model_dump_json().encode()
    return support.framed(header, b"PBW\x01\x05") + support.END


def toolchain(
    volume: Path, *, on_the_process_clock: bool = False, **overrides: object
) -> WorkerToolchain:
    """A client of the worker the volume names, whose port nothing serves yet.

    It runs on a fresh moved time unless the caller hands it one or asks for
    the time a client is built with by default.
    """
    base = WorkerToolchain(
        host=LOCAL_ADDRESS,
        port=port_of(volume),
        key=KEY,
        megabytes=MEGABYTES,
        reply_bound_seconds=2.0,
        connect_window_seconds=WINDOW,
        first_retry_seconds=0.02,
        longest_retry_seconds=0.1,
        reply_margin_seconds=0.2,
    )
    moved: dict[str, object] = {} if on_the_process_clock else {"time": MovedTime()}
    return replace(base, **(moved | overrides))


def a_step(root: Path, kind: StepKind = StepKind.BUILD) -> BuildStep:
    place = BuildPlace(root=root / "builds", run=Path("run"))
    (place.here / DECK_DIRECTORY).mkdir(parents=True, exist_ok=True)
    (place.here / DECK_DIRECTORY / "slides.md").write_text("# hello")
    (place.here / OUTPUT_DIRECTORY).mkdir(exist_ok=True)
    return BuildStep(
        place=place,
        slug="my-deck",
        name=kind,
        arguments=(),
        watched=lambda: None,
        line=StepTimer("build_step.ended", logging.getLogger(__name__)),
    )


def ended_line(step: BuildStep, logged: LoggedText) -> LogLine:
    """The one `build_step.ended` line of a step a runner has finished with."""
    step.line.end()
    (line,) = [one for one in logged.lines if one["event"] == "build_step.ended"]
    return line


def tree_of(root: Path) -> dict[str, bytes | None]:
    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def staging_of(step: BuildStep) -> Path:
    return step.place.here / f"{STAGED_PREFIX}{step.name}"


@dataclass
class MovedTime:
    """The client's monotonic time, which only the test and the client's waits move."""

    seconds: float = 0.0

    def now(self) -> float:
        """The moment the test or the last wait left it at."""
        return self.seconds

    def wait(self, seconds: float) -> None:
        """Move on by the wait at once."""
        self.seconds += seconds


@dataclass
class Seen:
    """What the fake worker saw of its clients."""

    steps: list[StepHeader] = field(default_factory=list[StepHeader])
    uploads: list[Path] = field(default_factory=list[Path])


type Script = Callable[[socket.socket, Seen, Path], None]


def proven(script: Script, key: bytes = KEY) -> Script:
    """A worker that runs the script only for a client whose hello verifies."""

    def serve(connection: socket.socket, seen: Seen, uploads: Path) -> None:
        verify_hello(connection, key, seconds=HELLO_SECONDS)
        script(connection, seen, uploads)

    return serve


def answering(
    reply: Callable[[socket.socket, StepHeader], None], key: bytes = KEY
) -> Script:
    """A worker that takes a STEP after the hello by answering and replying."""

    def serve(connection: socket.socket, seen: Seen, uploads: Path) -> None:
        request = read_request(connection)
        send_status(connection)
        seen.steps.append(request)
        upload = uploads / str(len(seen.steps))
        upload.mkdir()
        seen.uploads.append(upload)
        descriptor = os.open(upload, os.O_RDONLY | os.O_DIRECTORY)
        try:
            receive_entries(connection, descriptor, MEGABYTES)
        finally:
            os.close(descriptor)
        reply(connection, request)

    return proven(serve, key)


@contextmanager
def fake_worker(volume: Path, script: Script, accepts: int = 4) -> Generator[Seen]:
    """A thread serving the script on the volume's port."""
    listener = support.listening_with(volume, accepts)
    seen = Seen()
    uploads = volume / "uploads"
    uploads.mkdir()

    stopped = threading.Event()

    def serve() -> None:
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except support.StopServingError, OSError:
                return
            with connection, suppress(OSError, WireRefusedError):
                if not stopped.is_set():
                    script(connection, seen, uploads)

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield seen
    finally:
        stopped.set()
        listener.accepts_left = 1
        with socket.create_connection((support.LOCAL_ADDRESS, support.port_of(volume))):
            pass
        thread.join()
        listener.close()


def hanging_up(_connection: socket.socket, _seen: Seen, _uploads: Path) -> None:
    return None


def counting(connections: list[None], then: Script) -> Script:
    """A worker that notes every connection it serves, then runs the script."""

    def serve(connection: socket.socket, seen: Seen, uploads: Path) -> None:
        connections.append(None)
        then(connection, seen, uploads)

    return serve
