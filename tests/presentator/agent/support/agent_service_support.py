"""Fakes and builders the answerer's tests share: a service on a socket, tokens."""

from __future__ import annotations

import asyncio
import logging
import socket
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, NoReturn, Self
from uuid import UUID, uuid4

from agent_runtime_client.sse import encode_event
from agent_runtime_client.wire import (
    PROTOCOL_VERSION,
    AssistantText,
    Cancelled,
    Failed,
    FailureCode,
    Heartbeat,
    RejectionCode,
    RouteKind,
    RunFile,
    RunRejection,
    RunRequest,
    RunStarted,
    Succeeded,
)

from presentator.agent.backend import ServiceAnswerer, agent_config
from presentator.contracts.copresenter import CoPresenterUnavailableError
from tests.presentator.adapters.support.serving import Serving, loopback_listener

if TYPE_CHECKING:
    from types import TracebackType

    import pytest
    from agent_runtime_client.wire import RunEvent
    from starlette.types import Receive, Scope, Send

    from presentator.ports.copresenter import TokenSource

ORIGIN = "http://agent-service.test"
PERSON = "person-token-MARKER"
WORKLOAD = "workload-token-MARKER"
QUESTION = "question-MARKER"
SERVICE_MESSAGE = "service-message-MARKER"
CONTEXT = "context-MARKER"
VIOLATION_DETAIL = "violation-MARKER"
MARKERS = (
    PERSON,
    WORKLOAD,
    QUESTION,
    SERVICE_MESSAGE,
    VIOLATION_DETAIL,
    CONTEXT,
)
EVENT_STREAM = {"content-type": "text/event-stream"}
LOGGER = "presentator.agent.backend"
_SCOPE = "agent-service-audience"
_SERVER_WAIT_SECONDS = 10

type Step = Callable[[UUID, int], RunEvent]


def run_started() -> Step:
    return lambda run, _seq: RunStarted(
        v=PROTOCOL_VERSION,
        run_id=run,
        seq=0,
        model="anthropic/sonnet",
        route=RouteKind.API,
        heartbeat_interval_s=15,
    )


def assistant_text(text: str) -> Step:
    return lambda run, seq: AssistantText(
        v=PROTOCOL_VERSION, run_id=run, seq=seq, text=text
    )


def heartbeat() -> Step:
    return lambda run, seq: Heartbeat(v=PROTOCOL_VERSION, run_id=run, seq=seq)


def succeeded() -> Step:
    return lambda run, seq: Succeeded(
        v=PROTOCOL_VERSION, run_id=run, seq=seq, text="fertig", files=()
    )


def succeeded_with_a_file_and_no_words() -> Step:
    return lambda run, seq: Succeeded(
        v=PROTOCOL_VERSION,
        run_id=run,
        seq=seq,
        text="",
        files=(RunFile(name="notes.txt", content=b"Notizen"),),
    )


def failed(code: FailureCode) -> Step:
    return lambda run, seq: Failed(
        v=PROTOCOL_VERSION, run_id=run, seq=seq, code=code, message=SERVICE_MESSAGE
    )


def cancelled() -> Step:
    return lambda run, seq: Cancelled(v=PROTOCOL_VERSION, run_id=run, seq=seq)


def event_frames(*steps: Step) -> list[bytes]:
    run = uuid4()
    return [encode_event(step(run, seq)) for seq, step in enumerate(steps)]


def whole(frames: list[bytes]) -> list[bytes]:
    return [b"".join(frames)]


def byte_by_byte(frames: list[bytes]) -> list[bytes]:
    return [bytes([byte]) for byte in b"".join(frames)]


GOOD_ANSWER = (
    run_started(),
    assistant_text("Hallo "),
    heartbeat(),
    assistant_text("Welt."),
    succeeded(),
)


@dataclass
class Reply:
    """What the fake service answers one run with."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=lambda: dict(EVENT_STREAM))
    chunks: list[bytes] = field(default_factory=list[bytes])
    breaks_after_chunks: bool = False
    hangs_after_chunks: bool = False
    refuses_connection: bool = False
    late_chunks: list[bytes] = field(default_factory=list[bytes])
    gate: threading.Event | None = None


def reply_with(*chunks: bytes) -> Reply:
    return Reply(chunks=list(chunks))


def refusal_reply(code: RejectionCode, status: int, **headers: str) -> Reply:
    body = RunRejection(
        v=PROTOCOL_VERSION, code=code, message=SERVICE_MESSAGE
    ).model_dump_json()
    return Reply(
        status=status,
        headers={"content-type": "application/json", **headers},
        chunks=[body.encode()],
    )


@dataclass(frozen=True)
class ReceivedRun:
    """One request as it reached the fake service."""

    method: str
    path: str
    headers: dict[str, str]
    content: bytes


class Wire:
    """A fake agent service on a loopback socket for the length of a ``with``.

    It plays one reply, or one chosen by the question, and records every request.
    ``hung`` says it stopped sending; ``closed`` says the caller then left.
    """

    def __init__(self, reply: Reply | Callable[[str], Reply]) -> None:
        """Answer every request with this reply."""
        self._reply = reply
        self.requests: list[ReceivedRun] = []
        self.hung = threading.Event()
        self.closed = threading.Event()
        self.origin = ""
        self._stop_serving: Callable[[], object] | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> Self:
        """Serve on a thread of its own, so a blocking caller can reach it."""
        listener = loopback_listener()
        self.origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        if isinstance(self._reply, Reply) and self._reply.refuses_connection:
            self.origin = closed_port_origin()
        serving = threading.Event()

        async def serve() -> None:
            loop, stop = asyncio.get_running_loop(), asyncio.Event()
            self._stop_serving = lambda: loop.call_soon_threadsafe(stop.set)
            async with Serving(self, listener):
                serving.set()
                await stop.wait()

        self._thread = threading.Thread(
            target=asyncio.run, args=(serve(),), daemon=True
        )
        self._thread.start()
        assert serving.wait(_SERVER_WAIT_SECONDS)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop serving; what it recorded stays readable."""
        assert self._stop_serving is not None
        assert self._thread is not None
        self._stop_serving()
        self._thread.join(_SERVER_WAIT_SECONDS)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer one ASGI request as the served app."""
        if scope["type"] != "http":
            return
        content = await _whole_body(receive)
        self.requests.append(
            ReceivedRun(
                scope["method"],
                scope["path"],
                {name.decode(): value.decode() for name, value in scope["headers"]},
                content,
            )
        )
        reply = self._reply_to(content)
        await send(
            {
                "type": "http.response.start",
                "status": reply.status,
                "headers": [
                    (name.encode(), value.encode())
                    for name, value in reply.headers.items()
                ],
            }
        )
        await _send_chunks(send, reply.chunks)
        if reply.gate is not None:
            await asyncio.to_thread(reply.gate.wait, _SERVER_WAIT_SECONDS)
        await _send_chunks(send, reply.late_chunks)
        if reply.breaks_after_chunks:
            raise RuntimeError
        if reply.hangs_after_chunks:
            self.hung.set()
            await _until_disconnected(receive)
            self.closed.set()
            return
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    def _reply_to(self, content: bytes) -> Reply:
        if isinstance(self._reply, Reply):
            return self._reply
        return self._reply(
            RunRequest.model_validate_json(content).turn.messages[0].text
        )


async def _whole_body(receive: Receive) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        body.extend(message.get("body", b""))
        if not message.get("more_body"):
            return bytes(body)


async def _send_chunks(send: Send, chunks: list[bytes]) -> None:
    for chunk in chunks:
        await send({"type": "http.response.body", "body": chunk, "more_body": True})


async def _until_disconnected(receive: Receive) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass


class RaisedBeforeAwaitingError(RuntimeError):
    """An error a token callable raises before it returns an awaitable."""


class CustomBaseError(BaseException):
    """A BaseException subclass of a supplier's own."""


class Token:
    """A token callable that counts its calls and may raise instead.

    A raised error carries a marker as its ``__context__``.
    """

    def __init__(self, value: str | BaseException) -> None:
        """Hand out this token, or raise this error."""
        self._value = value
        self.calls = 0

    def __call__(self) -> Awaitable[str]:
        self.calls += 1
        if isinstance(self._value, RaisedBeforeAwaitingError):
            self._raise(self._value)
        return self._read()

    async def _read(self) -> str:
        if isinstance(self._value, BaseException):
            self._raise(self._value)
        return self._value

    @staticmethod
    def _raise(error: BaseException) -> NoReturn:
        error.__context__ = RuntimeError(CONTEXT)
        raise error


@dataclass
class Outcome:
    """What one ask yielded and how it ended."""

    texts: list[str]
    error: CoPresenterUnavailableError | None


async def ask_once(
    answerer: ServiceAnswerer,
    person: Callable[[], Awaitable[str]],
    *,
    message: str,
) -> Outcome:
    texts: list[str] = []
    try:
        async with answerer.answer(message=message, person_token=person) as stream:
            while True:
                try:
                    texts.append(await anext(stream))
                except StopAsyncIteration:
                    break
    except CoPresenterUnavailableError as error:
        return Outcome(texts, error)
    return Outcome(texts, None)


def an_answerer(
    origin: str,
    workload_token: TokenSource,
    *,
    monotonic: Callable[[], float] = time.monotonic,
) -> ServiceAnswerer:
    config = agent_config(backend="service", service_url=origin, workload_scope=_SCOPE)
    return ServiceAnswerer(
        config=config, workload_token=workload_token, monotonic=monotonic
    )


@dataclass
class Asker:
    """One ask against a fake service: its token callables and its wire."""

    person: Token
    workload: Token
    wire: Wire
    monotonic: Callable[[], float]

    def answerer(self) -> ServiceAnswerer:
        return an_answerer(self.wire.origin, self.workload, monotonic=self.monotonic)

    async def ask(self, *, message: str = QUESTION) -> Outcome:
        with self.wire:
            return await ask_once(self.answerer(), self.person, message=message)


def an_asker(
    reply: Reply,
    *,
    person: str | BaseException = PERSON,
    workload: str | BaseException = WORKLOAD,
    monotonic: Callable[[], float] = time.monotonic,
) -> Asker:
    return Asker(Token(person), Token(workload), Wire(reply), monotonic)


def unavailable(outcome: Outcome) -> CoPresenterUnavailableError:
    assert outcome.error is not None
    assert outcome.error.__cause__ is None
    assert outcome.error.__context__ is None
    return outcome.error


def warning_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.WARNING
    ]


def summary_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER and record.levelno == logging.INFO
    ]


def one_warning(caplog: pytest.LogCaptureFixture) -> str:
    (warning,) = warning_lines(caplog)
    return warning


def closed_port_origin() -> str:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{listener.getsockname()[1]}"


def violating_frame() -> bytes:
    return (
        b"id: 0\nevent: run_started\n"
        b'data: {"v": "violation-MARKER", "type": "run_started"}\n\n'
    )
