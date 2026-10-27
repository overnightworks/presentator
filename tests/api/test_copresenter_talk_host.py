"""The co-presenter is the talk host's: its session, token, Origin and person."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from starlette.websockets import WebSocketDisconnect
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.api.copresenter import CoPresenterSurface
from presentator.application.copresenter import AskLimiter
from presentator.contracts.models import Role, User
from tests.api.copresenter_support import (
    HEARING,
    POLICY_VIOLATION,
    QUESTION,
    TALK_ORIGIN,
    ManualDeadline,
    PrivateFakes,
    a_copresenter_talk_host,
    a_use,
)
from tests.api.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    NOW,
    USERNAME,
    a_web_auth,
    hand_over_session_cookies,
)
from tests.api.talk_support import A_DECK, MEMBER, A, Hosts, two_hosts
from tests.presentator.application.support.fakes import FakeDeckStore, FrozenClock

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

_JSON = {"accept": "application/json"}
_ASK = "/copresenter/ask"


def _hear_with_origin(browser: TestClient, origin: str | None) -> int | None:
    """The code the test client reports for a handshake the server denied before accept.

    On the wire that is an HTTP 403; None when the hearing was accepted.
    """
    headers = {} if origin is None else {"origin": origin}
    try:
        with browser.websocket_connect(HEARING, headers=headers):
            return None
    except WebSocketDisconnect as refused:
        return refused.code


# C4.
@pytest.mark.parametrize("role", [Role.ADMIN, Role.USER])
def test_who_answers_every_signed_in_person(role: Role) -> None:
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=role)

    answer = talk.client.get("/copresenter/who", headers=_JSON)

    assert answer.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


# C5.
def test_ask_streams_for_a_signed_in_person_with_the_browsers_own_provenance() -> None:
    talk, _private, _clock = a_copresenter_talk_host()
    talk.sign_in()

    answer = talk.client.post(_ASK, json=QUESTION)

    assert answer.status_code == HTTPStatus.OK


# C5.
@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"sec-fetch-site": "cross-site"}, id="cross-site"),
        pytest.param(
            {"origin": A_BROWSERS_ADDRESS, "sec-fetch-site": "same-site"},
            id="the-lobby-as-origin",
        ),
    ],
)
def test_a_post_from_another_site_is_refused_whatever_session_it_carries(
    claims: dict[str, str],
) -> None:
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in()

    refused = talk.client.post(_ASK, json=QUESTION, headers=claims)

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert private.answers.messages == []


# C5.
def test_an_ask_carrying_only_the_talk_hosts_referer_is_refused() -> None:
    """#474 cell 8: a Referer alone is no provenance, whichever host it names."""
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in()
    del talk.client.headers["sec-fetch-site"]

    refused = talk.client.post(
        _ASK, json=QUESTION, headers={"referer": f"{TALK_ORIGIN}/deck"}
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert private.answers.messages == []


# C5.
def test_an_ask_with_neither_provenance_header_is_refused_by_the_origin_check() -> None:
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in()
    del talk.client.headers["sec-fetch-site"]

    refused = talk.client.post(_ASK, json=QUESTION)

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert refused.json() == {"detail": "Missing Origin header"}
    assert private.answers.messages == []


# C6.
@pytest.mark.parametrize(
    ("origin", "denied_with"),
    [
        pytest.param(TALK_ORIGIN, None, id="the-talk-origin"),
        pytest.param(A_BROWSERS_ADDRESS, POLICY_VIOLATION, id="the-lobby-origin"),
        pytest.param(None, POLICY_VIOLATION, id="none"),
        pytest.param(
            TALK_ORIGIN.replace("https://", "http://"),
            POLICY_VIOLATION,
            id="the-talk-host-over-http",
        ),
        pytest.param("https://elsewhere.example.test", POLICY_VIOLATION, id="foreign"),
    ],
)
def test_hear_is_accepted_only_from_the_talk_origin(
    origin: str | None, denied_with: int | None
) -> None:
    talk, _private, _clock = a_copresenter_talk_host()
    talk.sign_in()

    assert _hear_with_origin(talk.client, origin) == denied_with


def test_hear_is_accepted_for_a_user_from_the_talk_origin() -> None:
    """#537 sentence 2: a hearing is accepted for a signed-in user."""
    talk, _private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=Role.USER)

    assert _hear_with_origin(talk.client, TALK_ORIGIN) is None


# C7.
def test_the_lobbys_session_cookie_does_not_sign_in_at_the_talk_host() -> None:
    talk, private, clock = a_copresenter_talk_host()
    session_id = talk.people.open_session_for(
        User(id="id-of-an-admin", username=USERNAME, role=Role.ADMIN)
    )
    hand_over_session_cookies(talk.client, session_id, a_web_auth(clock))
    assert DEFAULT_SESSION_COOKIE_NAME in talk.client.cookies

    answer = talk.client.get("/copresenter/who", headers=_JSON)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert private.speech.readiness_calls == 0


def test_a_sign_in_at_the_talk_host_sets_no_csrf_cookie(tmp_path: Path) -> None:
    """#474 cell 25: the session cookie is set and no cookie named csrf is."""
    hosts = two_hosts(tmp_path)

    returned = hosts.sign_in(hosts.browser(A_TALK_ADDRESS), A_TALK_ADDRESS)

    cookies = returned.headers.get_list("set-cookie")
    assert any(
        cookie.startswith(f"{DEFAULT_SESSION_COOKIE_NAME}=") for cookie in cookies
    )
    assert not any("csrf" in cookie.lower() for cookie in cookies)


def test_an_admin_demoted_while_signed_in_is_still_answered() -> None:
    """#537 sentence 3: a role change does not end the answer."""
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=Role.ADMIN)
    (admin,) = talk.people.users.users.values()
    talk.people.users.users[admin.id] = replace(admin, role=Role.USER)

    answer = talk.client.get("/copresenter/who", headers=_JSON)

    assert answer.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


def _talk_host_with_a_signed_in_admin(
    tmp_path: Path, deadline: ManualDeadline | None = None
) -> tuple[Hosts, TestClient, PrivateFakes]:
    private = PrivateFakes()
    surface = CoPresenterSurface(
        use=a_use(private, FakeDeckStore()),
        limiter=AskLimiter(clock=FrozenClock(instant=NOW)),
        browser_origin=TALK_ORIGIN,
        delay=asyncio.sleep if deadline is None else deadline.wait,
    )
    hosts = two_hosts(tmp_path, copresenter=lambda _store, _sessions, _auth: surface)
    return hosts, hosts.signed_in_at(A_TALK_ADDRESS), private


def _demote_at_the_lobby(hosts: Hosts) -> None:
    """The admin signs in at the lobby with the issuer's groups no longer admin."""
    lobby = hosts.browser(A_BROWSERS_ADDRESS)
    hosts.sign_in(lobby, A_BROWSERS_ADDRESS, subject=A.id, groups=MEMBER)


def test_a_lobby_sign_in_that_demotes_the_admin_keeps_the_deck_and_who(
    tmp_path: Path,
) -> None:
    """#537 sentence 3: a role change does not end the deck or who."""
    hosts, talk, private = _talk_host_with_a_signed_in_admin(tmp_path)
    _demote_at_the_lobby(hosts)

    deck = talk.get(f"/deck/{A_DECK}/")
    who = talk.get("/copresenter/who", headers=_JSON)

    assert deck.status_code == HTTPStatus.OK
    assert who.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


def test_a_lobby_sign_in_that_demotes_the_admin_keeps_the_running_hearing(
    tmp_path: Path,
) -> None:
    """#537 sentence 3: a role change does not end a running hearing."""
    deadline = ManualDeadline()
    hosts, talk, private = _talk_host_with_a_signed_in_admin(tmp_path, deadline)

    with talk.websocket_connect(HEARING, headers={"origin": TALK_ORIGIN}) as socket:
        assert deadline.started.wait(timeout=1)
        _demote_at_the_lobby(hosts)
        deadline.release_first()
        socket.send_bytes(b"after demotion")
        assert socket.receive_json() == {"text": "gehört", "final": True}

    assert private.speech.hearing.frames == [b"after demotion"]


# C9.
@pytest.mark.parametrize(
    ("accept", "status"),
    [
        pytest.param("text/html", HTTPStatus.FOUND, id="page"),
        pytest.param("application/json", HTTPStatus.UNAUTHORIZED, id="json"),
    ],
)
def test_a_signed_out_request_for_a_talk_is_sent_to_sign_in_or_refused(
    tmp_path: Path, accept: str, status: HTTPStatus
) -> None:
    hosts: Hosts = two_hosts(tmp_path)
    browser = hosts.browser(A_TALK_ADDRESS, headers={"accept": accept})

    answer = browser.get(f"/deck/{A_DECK}/")

    assert answer.status_code == status
    if status == HTTPStatus.FOUND:
        assert answer.headers["location"].startswith("/login/oidc?next=")
