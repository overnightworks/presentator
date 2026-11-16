"""Two processes of the ask route on one Redis: one answer per session at a time."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from urllib.parse import urlsplit

import httpx2

from tests.presentator.adapters.support.ask_lease_process import (
    FINISH_PATH,
    HEALTH_PATH,
    LEASE_VARIABLE,
    URL_VARIABLE,
)
from tests.presentator.host.support.app_processes import two_free_ports
from tests.support.copresenter_support import ASK, QUESTION
from tests.support.redis import relay_in_front_of
from tests.support.two_processes import TwoProcesses, two_modules

_MODULE = "tests.presentator.adapters.support.ask_lease_process"
_LEASE_MILLISECONDS = 2000
_REQUEST_SECONDS = 30
_START_SECONDS = 60
_POLL_SECONDS = 0.1


@dataclass(frozen=True, slots=True)
class _Process:
    """One running process of the ask route."""

    port: int

    def post(self, path: str) -> httpx2.Response:
        return httpx2.post(
            f"http://127.0.0.1:{self.port}{path}",
            json=QUESTION if path == ASK else None,
            timeout=_REQUEST_SECONDS,
        )

    def stream_ask(self) -> httpx2.Response:
        """The answer's whole body, read until the process ends it."""
        with httpx2.stream(
            "POST",
            f"http://127.0.0.1:{self.port}{ASK}",
            json=QUESTION,
            timeout=_REQUEST_SECONDS,
        ) as response:
            response.read()
            return response

    def let_answers_finish(self) -> None:
        assert self.post(FINISH_PATH).status_code == HTTPStatus.OK


def _wait_until_serving(port: int, process: object) -> None:
    deadline = time.monotonic() + _START_SECONDS
    while time.monotonic() < deadline:
        try:
            if httpx2.get(f"http://127.0.0.1:{port}{HEALTH_PATH}").is_success:
                return
        except httpx2.TransportError:
            pass
        time.sleep(_POLL_SECONDS)
    message = f"the process {process!r} never served"
    raise AssertionError(message)


@contextmanager
def _serving(redis_url: str) -> Generator[tuple[_Process, _Process, TwoProcesses]]:
    first_port, second_port = two_free_ports()
    with two_modules(
        _MODULE,
        [str(first_port)],
        {URL_VARIABLE: redis_url, LEASE_VARIABLE: str(_LEASE_MILLISECONDS)},
        second_arguments=[str(second_port)],
    ) as processes:
        _wait_until_serving(first_port, processes.first)
        _wait_until_serving(second_port, processes.second)
        yield _Process(first_port), _Process(second_port), processes


def _output_of(processes: TwoProcesses) -> str:
    written: list[str] = []
    for each in (processes.first, processes.second):
        each.kill()
        written.append(each.communicate(timeout=_REQUEST_SECONDS)[0])
    return "".join(written)


def test_the_other_process_answers_once_the_streaming_one_ends(
    redis_url: str,
) -> None:
    with _serving(redis_url) as (first, second, _processes):
        second.let_answers_finish()
        with httpx2.stream(
            "POST", f"http://127.0.0.1:{first.port}{ASK}", json=QUESTION
        ) as streaming:
            busy = second.post(ASK)
            first.let_answers_finish()
            streaming.read()
        after_the_release = second.stream_ask()

    assert streaming.status_code == HTTPStatus.OK
    assert busy.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert busy.headers["retry-after"] == "1"
    assert after_the_release.status_code == HTTPStatus.OK


def test_the_lease_of_a_holder_that_died_expires_at_the_other_process(
    redis_url: str,
) -> None:
    with _serving(redis_url) as (first, second, processes):
        second.let_answers_finish()
        with httpx2.stream(
            "POST", f"http://127.0.0.1:{first.port}{ASK}", json=QUESTION
        ):
            processes.first.kill()
            processes.first.wait()
            statuses = [second.post(ASK).status_code]
            deadline = time.monotonic() + _START_SECONDS
            while statuses[-1] != HTTPStatus.OK and time.monotonic() < deadline:
                time.sleep(_POLL_SECONDS)
                statuses.append(second.post(ASK).status_code)

    assert statuses[0] == HTTPStatus.TOO_MANY_REQUESTS
    assert statuses[-1] == HTTPStatus.OK


def test_an_ask_while_redis_cannot_be_reached_is_refused_visibly_and_leaks_nothing(
    redis_url: str,
) -> None:
    with (
        relay_in_front_of(redis_url) as redis,
        _serving(redis.url) as (
            first,
            second,
            processes,
        ),
    ):
        authority = urlsplit(redis.url).netloc
        redis.sever()
        refused = [first.post(ASK), second.post(ASK)]
        written = _output_of(processes)

    assert [each.status_code for each in refused] == [
        HTTPStatus.SERVICE_UNAVAILABLE
    ] * 2
    assert not [each for each in refused if authority in each.text]
    assert "ask lease failed" in written
    assert authority not in written


def test_an_answer_finishing_while_redis_is_out_ends_whole_and_leaks_nothing(
    redis_url: str,
) -> None:
    with (
        relay_in_front_of(redis_url) as redis,
        _serving(redis.url) as (
            first,
            _second,
            processes,
        ),
    ):
        with httpx2.stream(
            "POST",
            f"http://127.0.0.1:{first.port}{ASK}",
            json=QUESTION,
            timeout=_REQUEST_SECONDS,
        ) as streaming:
            authority = urlsplit(redis.url).netloc
            redis.sever()
            first.let_answers_finish()
            body = "".join(streaming.iter_text())
        written = _output_of(processes)

    assert "event: done" in body
    assert authority not in body
    assert "ask lease release failed" in written
    assert authority not in written
