"""A hearing socket's ended line names its person, client and trace."""

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import pytest
from logline import WEBSOCKET_EVENT

from tests.presentator.host.support.app_processes import (
    TALK_HOST,
    App,
    Browser,
    serving_apps,
)
from tests.presentator.host.support.copresenter_services import (
    HearingSocket,
    HeldGateway,
    a_gateway_and_a_speech_service,
)
from tests.support.lobby import A_TALK_ADDRESS, AN_API_GROUP
from tests.support.log_lines import LogLine
from tests.support.sign_in_support import ADMIN

_THE_CLIENT = "203.0.113.7"
_THE_PROXY = "127.0.0.1"
_A_NETWORK_NOT_THE_PEERS = "10.0.0.0/8"
_BEHIND_THE_PROXY = {"x-forwarded-for": _THE_CLIENT}


_THE_SESSION_EVENT = "hearing_session.ended"


def _ended_lines_of_both(app: App) -> tuple[LogLine, LogLine]:
    """The socket's ended line and the hearing session's, in either order."""
    seen: dict[str, LogLine] = {}

    def both_seen(line: LogLine) -> bool:
        if line["event"] in {WEBSOCKET_EVENT, _THE_SESSION_EVENT}:
            seen[line["event"]] = line
        return seen.keys() == {WEBSOCKET_EVENT, _THE_SESSION_EVENT}

    app.log_line_where(both_seen)
    return seen[WEBSOCKET_EVENT], seen[_THE_SESSION_EVENT]


@pytest.fixture
def app(
    request: pytest.FixtureRequest,
    environment: pytest.MonkeyPatch,
    database_url: str,
    tmp_path: Path,
) -> Iterator[App]:
    """The served apps with a hearing behind them, trusting the parametrized proxies."""
    environment.setenv("PRESENTATOR_TRUSTED_PROXIES", request.param)
    with (
        a_gateway_and_a_speech_service(HeldGateway()) as services,
        serving_apps(database_url, tmp_path, services) as (first, _second),
    ):
        yield first


def _hear_once(app: App) -> None:
    person = Browser(host=TALK_HOST)
    person.sign_in(app, "alice", groups=[*ADMIN, AN_API_GROUP])

    hearing = HearingSocket(
        app.port,
        host=TALK_HOST,
        origin=A_TALK_ADDRESS,
        cookie=person.cookie_header,
        headers=_BEHIND_THE_PROXY,
    )
    with closing(hearing):
        assert hearing.hear_after_saying() != ""


@pytest.mark.parametrize(
    ("app", "ip"),
    [(_THE_PROXY, _THE_CLIENT), (_A_NETWORK_NOT_THE_PEERS, _THE_PROXY)],
    indirect=["app"],
    ids=["a trusted peer", "an untrusted peer"],
)
def test_a_hearing_sockets_ended_line_names_its_person_and_the_resolved_client(
    app: App, ip: str
) -> None:
    _hear_once(app)
    line, _session = _ended_lines_of_both(app)

    assert line["sub"] == "alice"
    assert line["ip"] == ip


@pytest.mark.parametrize("app", [_THE_PROXY], indirect=True)
def test_a_hearing_sockets_ended_line_shares_the_hearings_trace(app: App) -> None:
    _hear_once(app)
    line, session = _ended_lines_of_both(app)

    assert line["trace_id"] == session["trace_id"]
