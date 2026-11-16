"""Two app processes on one database, and a browser that reaches either."""

import json
import os
import queue
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Final

import httpx2
from pydantic import JsonValue

from presentator.api.auth import (
    LOGOUT_PATH,
    SIGN_IN_CALLBACK_PATH,
    START_SIGN_IN_PATH,
)
from tests.presentator.host.support.app_process import ANSWER_PREFIX
from tests.support.lobby import A_BROWSERS_ADDRESS
from tests.support.log_lines import LogLine
from tests.support.postgres import database_environment
from tests.support.redis import emptied_database_url
from tests.support.sign_in_support import ADMIN, MEMBER
from tests.support.two_processes import TwoProcesses, two_modules

_CHILD: Final = "tests.presentator.host.support.app_process"
_START_SECONDS: Final = 60
_REQUEST_SECONDS: Final = 30
_OUTPUT_END_SECONDS: Final = 30
_LAST_LINES: Final = 10
_POLL_SECONDS: Final = 0.1
_HOST: Final = A_BROWSERS_ADDRESS.removeprefix("https://")
_FROM_THE_SAME_ORIGIN: Final = {"sec-fetch-site": "same-origin"}
_A_FORM: Final = {"content-type": "application/x-www-form-urlencoded"}
SESSION: Final = "/api/session"
KEY_DRAFT: Final = "/api/sources/new/key-draft"


@dataclass(frozen=True, slots=True)
class App:
    """One running app process."""

    process: subprocess.Popen[str]
    port: int
    # One answer is taken per question, so questions go one at a time.
    asking: threading.Lock = field(default_factory=threading.Lock, compare=False)
    # The issuer's answers, read off the output as it comes; None ends the output.
    answers: queue.Queue[str | None] = field(
        default_factory=queue.Queue[str | None], compare=False
    )
    # The log lines it writes, in order; None ends the output.
    logged: queue.Queue[LogLine | None] = field(
        default_factory=queue.Queue[LogLine | None], compare=False
    )

    # Every line the process wrote, in order, and whether its output has ended.
    written: list[str] = field(default_factory=list[str], compare=False)
    ended: threading.Event = field(default_factory=threading.Event, compare=False)

    def __post_init__(self) -> None:
        """Start reading the output, so a long log never fills the pipe."""
        threading.Thread(target=self._drain_output, daemon=True).start()

    def _drain_output(self) -> None:
        """Keep the issuer's answers and the log lines until the output ends."""
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                self.written.append(line)
                if line.startswith(ANSWER_PREFIX):
                    self.answers.put(line.removeprefix(ANSWER_PREFIX).strip())
                elif line.startswith("{"):
                    self.logged.put(json.loads(line))
        finally:
            self.answers.put(None)
            self.logged.put(None)
            self.ended.set()

    def stop(self, within: float = _OUTPUT_END_SECONDS) -> None:
        """Kill the process, then wait until its output has been read to the end.

        The pipe stays open until then, so the drain never reads a closed file.
        """
        self.process.kill()
        self.process.wait(timeout=within)
        if not self.ended.wait(timeout=within):
            last = "".join(self.written[-_LAST_LINES:])
            message = (
                f"the output of the app process on port {self.port} never ended;"
                f" its last lines:\n{last}"
            )
            raise AssertionError(message)

    def output_after_exit(self) -> str:
        """Everything the process wrote, once it has exited and its output ended."""
        assert self.ended.wait(timeout=_REQUEST_SECONDS), "the output never ended"
        return "".join(self.written)

    def log_line_where(self, wanted: Callable[[LogLine], bool]) -> LogLine:
        """The first log line from here on that `wanted` accepts."""
        while True:
            line = self.logged.get(timeout=_REQUEST_SECONDS)
            if line is None:
                message = "the app process ended without writing the line"
                raise AssertionError(message)
            if wanted(line):
                return line

    def request(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        body: JsonValue | None = None,
    ) -> httpx2.Response:
        """One request for the lobby's hostname, redirects left alone."""
        return httpx2.request(
            method,
            f"http://127.0.0.1:{self.port}{path}",
            headers={"host": _HOST, **headers},
            json=body,
            timeout=_REQUEST_SECONDS,
            follow_redirects=False,
        )

    def ask_issuer(
        self, authorization_url: str, subject: str, groups: list[str]
    ) -> str:
        """The query this process's issuer sends the browser back with."""
        with self.asking:
            return self._ask(authorization_url, subject, groups)

    def ask_access_token(
        self, subject: str, groups: list[str], authorized_party: str
    ) -> str:
        """An access token `authorized_party` holds for `subject`, from the issuer."""
        with self.asking:
            return self._send(
                {
                    "subject": subject,
                    "groups": groups,
                    "authorized_party": authorized_party,
                }
            )

    def _ask(self, authorization_url: str, subject: str, groups: list[str]) -> str:
        return self._send(
            {
                "authorization_url": authorization_url,
                "subject": subject,
                "groups": groups,
            }
        )

    def _send(self, question: dict[str, str | list[str]]) -> str:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(question) + "\n")
        self.process.stdin.flush()
        answer = self.answers.get(timeout=_REQUEST_SECONDS)
        if answer is None:
            message = "the app process ended without answering the issuer"
            raise AssertionError(message)
        return answer


@dataclass(slots=True)
class Browser:
    """The cookies of one person, who may go to either process."""

    cookies: dict[str, str] = field(default_factory=dict[str, str])

    def request(
        self,
        app: App,
        method: str,
        path: str,
        headers: Mapping[str, str] | None = None,
        body: JsonValue | None = None,
    ) -> httpx2.Response:
        """Send the cookies held, keep the ones the answer sets."""
        cookie = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        answer = app.request(
            method,
            path,
            {
                **_FROM_THE_SAME_ORIGIN,
                "accept": "application/json",
                **({"cookie": cookie} if cookie else {}),
                **(headers or {}),
            },
            body,
        )
        for header in answer.headers.get_list("set-cookie"):
            for name, morsel in SimpleCookie(header).items():
                if morsel["max-age"] == "0" or not morsel.value:
                    self.cookies.pop(name, None)
                else:
                    self.cookies[name] = morsel.value
        return answer

    def sign_in(self, app: App, subject: str, *, admin: bool = False) -> None:
        """Sign `subject` in through `app`, as a person does at the issuer."""
        started = self.request(app, "GET", START_SIGN_IN_PATH)
        query = app.ask_issuer(
            started.headers["location"], subject, ADMIN if admin else MEMBER
        )
        self.request(app, "GET", f"{SIGN_IN_CALLBACK_PATH}?{query}")

    def sign_out(self, app: App) -> None:
        self.request(app, "POST", LOGOUT_PATH, _A_FORM)

    def signed_in_as(self, app: App) -> str | None:
        """The username `app` shows for these cookies, or nothing."""
        person = self.request(app, "GET", SESSION).json()["person"]
        return None if person is None else person["username"]


def two_free_ports() -> tuple[int, int]:
    """Two loopback ports no listener holds now."""
    with closing(socket.socket()) as first, closing(socket.socket()) as second:
        first.bind(("127.0.0.1", 0))
        second.bind(("127.0.0.1", 0))
        return int(first.getsockname()[1]), int(second.getsockname()[1])


def _environment(
    database_url: str, directory: Path, extra: Mapping[str, str]
) -> dict[str, str]:
    app_settings = {
        name: value
        for name, value in os.environ.items()
        if name.startswith("PRESENTATOR_") and not name.startswith("PRESENTATOR_TEST_")
    }
    return {
        **app_settings,
        **database_environment(database_url, directory),
        # Both processes count their budgets in the one Redis database of this worker.
        "PRESENTATOR_REDIS_URL": emptied_database_url(),
        **extra,
    }


def _wait_until_ready(app: App) -> None:
    deadline = time.monotonic() + _START_SECONDS
    while time.monotonic() < deadline:
        assert app.process.poll() is None, "the app process ended while starting"
        try:
            if app.request("GET", "/health", {}).status_code == HTTPStatus.OK:
                return
        except httpx2.TransportError:
            pass
        time.sleep(_POLL_SECONDS)
    message = "the app process never became ready"
    raise AssertionError(message)


@contextmanager
def starting_apps(
    database_url: str,
    directory: Path,
    *,
    history: Path | None = None,
    environment: Mapping[str, str] | None = None,
    second_environment: Mapping[str, str] | None = None,
) -> Generator[tuple[TwoProcesses, tuple[int, int]]]:
    """Two app processes started on `database_url`, not waited for, and their ports.

    `history` replaces the migration history both know, as a release with a
    longer one would; `second_environment` is added for the second process alone.
    """
    ports = two_free_ports()
    extra = [] if history is None else [str(history)]
    with two_modules(
        _CHILD,
        [str(ports[0]), *extra],
        _environment(database_url, directory, environment or {}),
        second_arguments=[str(ports[1]), *extra],
        second_environment=second_environment,
    ) as processes:
        yield processes, ports


@contextmanager
def serving_apps(
    database_url: str,
    directory: Path,
    environment: Mapping[str, str] | None = None,
    second_environment: Mapping[str, str] | None = None,
) -> Generator[tuple[App, App]]:
    """Two app processes answering on one database, with `environment` added.

    `second_environment` is added for the second process alone.
    """
    with starting_apps(
        database_url,
        directory,
        environment=environment,
        second_environment=second_environment,
    ) as (processes, ports):
        first = App(processes.first, ports[0])
        second = App(processes.second, ports[1])
        try:
            _wait_until_ready(first)
            _wait_until_ready(second)
            yield first, second
        finally:
            try:
                first.stop()
            finally:
                second.stop()
