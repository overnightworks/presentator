"""The served app writes one JSON request line per request, through the real server."""

from collections.abc import Callable, Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from logline import REQUEST_EVENT

from presentator.api.deck_list_api import DECK_LIST_PATH
from presentator.host import main
from tests.presentator.host.support.app_processes import (
    SESSION,
    App,
    Browser,
    serving_apps,
)
from tests.support.log_lines import LogLine

_THE_CLIENT = "203.0.113.7"
_THE_PROXY = "127.0.0.1"
_A_RELEASE = "1.2.3"
_THE_CALLERS_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
_THE_CALLERS_PARENT = f"00-{_THE_CALLERS_TRACE}-00f067aa0ba902b7-01"
_BEHIND_THE_PROXY = {
    "x-forwarded-for": _THE_CLIENT,
    "traceparent": _THE_CALLERS_PARENT,
}


def _request_line_with(status: HTTPStatus) -> Callable[[LogLine], bool]:
    def wanted(line: LogLine) -> bool:
        return line["event"] == REQUEST_EVENT and line["status"] == status

    return wanted


def _request_line_of(route: str) -> Callable[[LogLine], bool]:
    def wanted(line: LogLine) -> bool:
        return line["event"] == REQUEST_EVENT and line["route"] == route

    return wanted


@pytest.fixture
def app(
    environment: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> Iterator[App]:
    environment.setenv("PRESENTATOR_TRUSTED_PROXIES", _THE_PROXY)
    with serving_apps(database_url, tmp_path, {"SERVICE_VERSION": _A_RELEASE}) as (
        first,
        _second,
    ):
        yield first


def test_a_signed_in_request_writes_one_request_line_naming_its_person_client_and_trace(
    app: App,
) -> None:
    person = Browser()
    person.sign_in(app, "alice")

    answer = person.request(app, "GET", SESSION, _BEHIND_THE_PROXY)
    line = app.log_line_where(_request_line_of(SESSION))

    assert answer.status_code == HTTPStatus.OK
    assert line["outcome"] == "ok"
    assert line["method"] == "GET"
    assert line["status"] == HTTPStatus.OK
    assert line["sub"] == "alice"
    assert line["ip"] == _THE_CLIENT
    assert line["trace_id"] == _THE_CALLERS_TRACE
    assert line["service"] == main.SERVICE_NAME
    assert line["version"] == _A_RELEASE
    assert isinstance(line["duration_ms"], int | float)
    assert not any(cookie in str(line) for cookie in person.cookies.values())


def test_a_refused_request_writes_its_request_line_with_the_client_and_no_person(
    app: App,
) -> None:
    answer = Browser().request(app, "GET", DECK_LIST_PATH, _BEHIND_THE_PROXY)
    line = app.log_line_where(_request_line_with(HTTPStatus.UNAUTHORIZED))

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert line["status"] == HTTPStatus.UNAUTHORIZED
    assert line["sub"] is None
    assert line["ip"] == _THE_CLIENT


def test_a_mounted_files_request_line_names_the_mount_and_never_the_file_path(
    app: App,
) -> None:
    path = f"/_app/{'a' * 40}.js"

    Browser().request(app, "GET", path, _BEHIND_THE_PROXY)
    line = app.log_line_where(_request_line_with(HTTPStatus.NOT_FOUND))

    assert isinstance(line["route"], str)
    assert path.startswith(line["route"])
    assert line["route"] != path
