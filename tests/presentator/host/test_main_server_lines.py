"""uvicorn's warnings and errors reach the log as literal events with codes."""

import asyncio
import socket
import threading
from collections.abc import Generator
from contextlib import closing, contextmanager
from http import HTTPStatus

import pytest
from starlette.types import Receive, Scope, Send
from uvicorn.config import STARTUP_FAILURE

from presentator.api.health import Stopping
from presentator.host import main
from presentator.host.stopping_server import (
    StoppingServer,
    name_uvicorn_records,
    served_config,
)
from tests.support.log_lines import LoggedText, events_of, json_logged

_LOOPBACK = "127.0.0.1"
_END_SECONDS = 10
# An address of the documentation range, which no host of the tests holds.
_AN_ADDRESS_NOT_HERE = "192.0.2.1"
_FAILS = "/fails/and/never/says/why"
_ANSWERS = "/answers"
_ANSWERS_NOTHING = "/answers/nothing"
_NOT_HTTP = b"\x16\x03\x01 these bytes are no HTTP request\r\n\r\n"


def _request(path: str, *, upgrade: bytes | None = None) -> bytes:
    lines = [b"GET " + path.encode() + b"?q=never HTTP/1.1", b"Host: x"]
    if upgrade is None:
        lines.append(b"Connection: close")
    else:
        lines += [b"Connection: Upgrade, close", b"Upgrade: " + upgrade]
    return b"\r\n".join([*lines, b"", b""])


class _AppFailedError(RuntimeError):
    pass


async def _app(scope: Scope, receive: Receive, send: Send) -> None:
    """Fails, answers or returns without an answer, by the path."""
    del receive
    if scope["type"] != "http":
        return
    if scope["path"] == _FAILS:
        message = "a failure whose text the log never quotes"
        raise _AppFailedError(message)
    if scope["path"] == _ANSWERS:
        await send({"type": "http.response.start", "status": HTTPStatus.NO_CONTENT})
        await send({"type": "http.response.body"})


@contextmanager
def _serving_in_a_thread() -> Generator[int]:
    """A real server over `_app`; yields its port.

    The socket listens before the server starts, so a connection waits in its
    backlog instead of racing the start.
    """
    name_uvicorn_records()
    listening = socket.socket()
    listening.bind((_LOOPBACK, 0))
    listening.listen()
    server = StoppingServer(served_config(_app, host=_LOOPBACK, port=0), Stopping())
    thread = threading.Thread(
        target=asyncio.run, args=(server.serve(sockets=[listening]),)
    )
    thread.start()
    try:
        yield listening.getsockname()[1]
    finally:
        server.should_exit = True
        thread.join(_END_SECONDS)
        listening.close()
    assert not thread.is_alive(), "the server thread outlived its stop"


def _sent(port: int, data: bytes) -> None:
    with socket.create_connection((_LOOPBACK, port), timeout=_END_SECONDS) as sent:
        sent.sendall(data)
        while sent.recv(4096):
            pass


@pytest.mark.parametrize(
    ("data", "event", "codes"),
    [
        (
            _NOT_HTTP,
            "server.refused",
            {"level": "info", "reason": "invalid_request"},
        ),
        (
            _request(_FAILS),
            "request.failed",
            {"level": "error", "error_type": _AppFailedError.__name__},
        ),
        (
            _request(_ANSWERS, upgrade=b"h2c"),
            "server.refused",
            {"level": "info", "reason": "unsupported_upgrade"},
        ),
        (
            _request(_ANSWERS_NOTHING),
            "server.reported",
            {"level": "error"},
        ),
    ],
)
def test_a_uvicorn_record_is_one_literal_event_with_codes_and_no_text(
    capsys: pytest.CaptureFixture[str],
    data: bytes,
    event: str,
    codes: dict[str, str],
) -> None:
    with json_logged(capsys) as logged, _serving_in_a_thread() as port:
        _sent(port, data)

    (line,) = [one for one in logged.lines if one["logger"] == "uvicorn.error"]
    assert line["event"] == event
    assert {key: line[key] for key in codes} == codes
    assert "exception" not in line
    assert "never" not in logged.text


@pytest.mark.parametrize(
    ("host", "reason"),
    [(_LOOPBACK, "port_taken"), (_AN_ADDRESS_NOT_HERE, "bind_refused")],
)
def test_a_start_that_cannot_listen_ends_in_one_failed_line_and_no_ready_line(
    environment: pytest.MonkeyPatch, logged: LoggedText, host: str, reason: str
) -> None:
    with closing(socket.socket()) as holder:
        holder.bind((_LOOPBACK, 0))
        holder.listen()
        environment.setenv("PRESENTATOR_HOST", host)
        environment.setenv("PRESENTATOR_PORT", str(holder.getsockname()[1]))

        with pytest.raises(SystemExit) as ended:
            main.main([])

    assert ended.value.code == STARTUP_FAILURE
    (failed,) = events_of(logged, "process.failed")
    assert (failed["level"], failed["reason"]) == ("error", reason)
    assert not events_of(logged, "process.ready")
    assert host not in logged.text
