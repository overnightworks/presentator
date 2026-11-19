"""uvicorn's records under the served config reach the log as coded events."""

import asyncio
import json
import logging
import socket
import threading
from collections.abc import Generator
from contextlib import closing, contextmanager
from http import HTTPStatus
from importlib.resources import files
from typing import override

import pytest
from starlette.types import Receive, Scope, Send
from uvicorn.config import STARTUP_FAILURE

from presentator.api.health import Stopping
from presentator.host import main
from presentator.host.lifecycle import StopLine
from presentator.host.stopping_server import StoppingServer, served_config
from tests.support.log_lines import LoggedText, LogLine, events_of, json_logged

_LOOPBACK = "127.0.0.1"
_END_SECONDS = 10
# An address of the documentation range, which no host of the tests holds.
_AN_ADDRESS_NOT_HERE = "192.0.2.1"
_FAILS = "/fails/and/stays/unquoted"
_ANSWERS = "/answers"
_ANSWERS_NOTHING = "/answers/nothing"
_WAITS_FOR_BODY = "/waits/for/a/body"
_NOT_HTTP = b"\x16\x03\x01 these bytes are no HTTP request\r\n\r\n"
# The query every request carries, which no line may quote.
_QUERY = "never"


def _base_keys() -> frozenset[str]:
    """The keys logline writes on every line, read from its published standard."""
    standard = json.loads((files("logline") / "standard.json").read_text())
    return frozenset(standard["base_keys"])


def _request(path: str, *, upgrade: bytes | None = None, body_length: int = 0) -> bytes:
    lines = [b"GET " + path.encode() + b"?q=" + _QUERY.encode() + b" HTTP/1.1"]
    lines += [b"Host: x", b"Content-Length: " + str(body_length).encode()]
    if upgrade is None:
        lines.append(b"Connection: close")
    else:
        lines += [b"Connection: Upgrade, close", b"Upgrade: " + upgrade]
    return b"\r\n".join([*lines, b"", b""])


class _AppFailedError(RuntimeError):
    pass


class _App:
    """Fails, answers, returns without an answer or waits for a body, by the path."""

    def __init__(self) -> None:
        """An app no request has reached yet."""
        self.waiting = threading.Event()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Answer one request by its path."""
        if scope["type"] != "http":
            return
        if scope["path"] == _FAILS:
            message = "a failure the app raises"
            raise _AppFailedError(message)
        if scope["path"] == _ANSWERS:
            await send({"type": "http.response.start", "status": HTTPStatus.NO_CONTENT})
            await send({"type": "http.response.body"})
        if scope["path"] == _WAITS_FOR_BODY:
            self.waiting.set()
            # The client never sends the body, so this returns only when it leaves.
            await receive()


class _EventSeen(logging.Handler):
    """Notes when logline has named a record on uvicorn's logger as `event`."""

    def __init__(self, event: str) -> None:
        """Watch for `event`."""
        super().__init__()
        self._event = event
        self.seen = threading.Event()

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if record.getMessage() == self._event:
            self.seen.set()


@contextmanager
def _serving_in_a_thread(app: _App) -> Generator[StoppingServer]:
    """The served config over `app`, on a port the server's `listening` holds.

    The socket listens before the server starts, so a connection waits in its
    backlog instead of racing the start.
    """
    listening = socket.socket()
    listening.bind((_LOOPBACK, 0))
    listening.listen()
    port = listening.getsockname()[1]
    server = StoppingServer(
        served_config(app, host=_LOOPBACK, port=port), Stopping(), StopLine()
    )
    thread = threading.Thread(
        target=asyncio.run, args=(server.serve(sockets=[listening]),)
    )
    thread.start()
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(_END_SECONDS)
        listening.close()
    assert not thread.is_alive(), "the server thread outlived its stop"


def _connected(server: StoppingServer) -> socket.socket:
    return socket.create_connection(
        (_LOOPBACK, server.config.port), timeout=_END_SECONDS
    )


def _sent(server: StoppingServer, data: bytes) -> None:
    with _connected(server) as sent:
        sent.sendall(data)
        while sent.recv(4096):
            pass


def _codes(line: LogLine) -> LogLine:
    """What a line carries besides logline's base keys and a traceback."""
    return {
        key: value
        for key, value in line.items()
        if key not in _base_keys() and key != "exception"
    }


@pytest.mark.parametrize(
    ("data", "event", "values", "carries_traceback"),
    [
        (
            _NOT_HTTP,
            "http_request.refused",
            {"level": "warning", "reason": "invalid_request"},
            False,
        ),
        (
            _request(_ANSWERS, upgrade=b"h2c"),
            "http_request.refused",
            {"level": "warning", "reason": "unsupported_upgrade"},
            False,
        ),
        (
            _request(_ANSWERS_NOTHING),
            "asgi_app.contract_broken",
            {"level": "error", "reason": "response_not_started"},
            False,
        ),
        (
            _request(_FAILS),
            "asgi_app.failed",
            {"level": "error", "error_type": _AppFailedError.__name__},
            True,
        ),
    ],
)
def test_a_served_request_uvicorn_reports_is_one_coded_event_without_its_query(
    capsys: pytest.CaptureFixture[str],
    data: bytes,
    event: str,
    values: dict[str, str],
    carries_traceback: bool,  # noqa: FBT001 - a parametrized case value
) -> None:
    with json_logged(capsys) as logged, _serving_in_a_thread(_App()) as server:
        _sent(server, data)

    (line,) = events_of(logged, event)
    codes = {"level": line["level"], **_codes(line)}
    assert codes == values
    assert ("exception" in line) == carries_traceback
    assert not events_of(logged, "uvicorn.unknown_record")
    assert _QUERY not in logged.text


def test_a_graceful_stop_with_an_open_connection_writes_one_coded_waiting_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = _App()
    waiting_line = _EventSeen("server_shutdown.waiting")
    uvicorn_logger = logging.getLogger("uvicorn.error")
    with json_logged(capsys) as logged, _serving_in_a_thread(app) as server:
        uvicorn_logger.addHandler(waiting_line)
        try:
            with _connected(server) as open_connection:
                open_connection.sendall(_request(_WAITS_FOR_BODY, body_length=1))
                assert app.waiting.wait(_END_SECONDS)
                server.should_exit = True
                assert waiting_line.seen.wait(_END_SECONDS)
        finally:
            uvicorn_logger.removeHandler(waiting_line)

    (line,) = [
        one
        for one in events_of(logged, "server_shutdown.waiting")
        if one.get("reason") == "connections"
    ]
    assert (line["level"], _codes(line)) == ("info", {"reason": "connections"})
    assert not events_of(logged, "uvicorn.unknown_record")
    assert _QUERY not in logged.text


@pytest.mark.usefixtures("served_database")
@pytest.mark.parametrize(
    ("host", "reason"),
    [(_LOOPBACK, "port_taken"), (_AN_ADDRESS_NOT_HERE, "os_error")],
)
def test_a_start_that_cannot_listen_ends_in_one_coded_bind_line_and_no_ready_line(
    environment: pytest.MonkeyPatch, logged: LoggedText, host: str, reason: str
) -> None:
    with closing(socket.socket()) as holder:
        holder.bind((_LOOPBACK, 0))
        holder.listen()
        port = str(holder.getsockname()[1])
        environment.setenv("PRESENTATOR_HOST", host)
        environment.setenv("PRESENTATOR_PORT", port)

        with pytest.raises(SystemExit) as ended:
            main.main([])

    assert ended.value.code == STARTUP_FAILURE
    (failed,) = events_of(logged, "server_bind.failed")
    codes = {key: value for key, value in failed.items() if key not in _base_keys()}
    assert codes == {"reason": reason, "error_type": OSError.__name__}
    assert failed["level"] == "error"
    assert not events_of(logged, "process.ready")
    assert host not in logged.text
    assert f":{port}" not in logged.text
