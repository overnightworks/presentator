"""The co-presenter is the talk host's: its session, token, Origin and person."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Engine
from starlette.websockets import WebSocketDisconnect
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.contracts.models import Role, User
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.copresenter_support import (
    HEARING,
    POLICY_VIOLATION,
    QUESTION,
    TALK_ORIGIN,
    ManualDeadline,
    PrivateFakes,
    a_copresenter_talk_host,
    two_hosts_with_a_copresenter,
    who,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    A_TALK_SOCKET_ADDRESS,
    USERNAME,
    a_web_auth,
    hand_over_session_cookies,
)
from tests.support.talk_support import A_DECK, MEMBER, A, Hosts, two_hosts

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from presentator.contracts.decks import Deck

TALK = f"{A_TALK_SOCKET_ADDRESS}/copresenter/talk?deck={A_DECK}&language=de"
_JSON = {"accept": "application/json"}
_ASK = "/copresenter/ask"


def _hear_with_origin(
    browser: TestClient, origin: str | None, address: str = HEARING
) -> int | None:
    """The code the test client reports for a handshake the server denied before accept.

    On the wire that is an HTTP 403; None when the hearing was accepted.
    """
    headers = {} if origin is None else {"origin": origin}
    try:
        with browser.websocket_connect(address, headers=headers):
            return None
    except WebSocketDisconnect as refused:
        return refused.code


# CP3
@pytest.mark.parametrize("role", [Role.ADMIN, Role.USER])
def test_who_answers_every_signed_in_person(role: Role) -> None:
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=role)

    answer = who(talk.client, headers=_JSON)

    assert answer.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


def test_ask_streams_for_a_signed_in_person_with_the_browsers_own_provenance() -> None:
    talk, _private, _clock = a_copresenter_talk_host()
    talk.sign_in()

    answer = talk.client.post(_ASK, json=QUESTION)

    assert answer.status_code == HTTPStatus.OK


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


def test_an_ask_carrying_only_the_talk_hosts_referer_is_refused() -> None:
    """A Referer alone is no provenance, whichever host it names."""
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in()
    del talk.client.headers["sec-fetch-site"]

    refused = talk.client.post(
        _ASK, json=QUESTION, headers={"referer": f"{TALK_ORIGIN}/deck"}
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert private.answers.messages == []


def test_an_ask_with_neither_provenance_header_is_refused_by_the_origin_check() -> None:
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in()
    del talk.client.headers["sec-fetch-site"]

    refused = talk.client.post(_ASK, json=QUESTION)

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert refused.json() == {"detail": "Missing Origin header"}
    assert private.answers.messages == []


# CP13
_REFUSED_ORIGINS = {
    "the-lobby-origin": A_BROWSERS_ADDRESS,
    "null": "null",
    "none": None,
    "the-talk-host-over-http": TALK_ORIGIN.replace("https://", "http://"),
    "foreign": "https://elsewhere.example.test",
}
_REFUSED_ORIGIN_CASES = [
    pytest.param(origin, id=name) for name, origin in _REFUSED_ORIGINS.items()
]


@pytest.mark.parametrize(
    ("origin", "denied_with"),
    [
        pytest.param(TALK_ORIGIN, None, id="the-talk-origin"),
        *(
            pytest.param(origin, POLICY_VIOLATION, id=name)
            for name, origin in _REFUSED_ORIGINS.items()
        ),
    ],
)
def test_hear_is_accepted_only_from_the_talk_origin(
    origin: str | None, denied_with: int | None
) -> None:
    talk, _private, _clock = a_copresenter_talk_host()
    talk.sign_in()

    assert _hear_with_origin(talk.client, origin) == denied_with


class _UnreadDecks(FakeDeckStore):
    """A deck store a refused talk must never reach."""

    def get(self, slug: str) -> Deck | None:
        message = f"a refused talk read the deck {slug}"
        raise AssertionError(message)


@pytest.mark.parametrize("origin", _REFUSED_ORIGIN_CASES)
def test_talk_is_refused_before_accept_from_every_origin_but_the_talk_origin(
    origin: str | None,
) -> None:
    talk, private, _clock = a_copresenter_talk_host(decks=_UnreadDecks())
    talk.sign_in()

    assert _hear_with_origin(talk.client, origin, TALK) == POLICY_VIOLATION
    assert private.speech.hearing_opens == 0


def test_talk_is_refused_before_accept_from_an_origin_the_surface_does_not_name() -> (
    None
):
    """The route holds the surface's origin itself, whatever the sign-in admits."""
    talk, private, _clock = a_copresenter_talk_host(
        decks=_UnreadDecks(), browser_origin=A_BROWSERS_ADDRESS
    )
    talk.sign_in()

    assert _hear_with_origin(talk.client, TALK_ORIGIN, TALK) == POLICY_VIOLATION
    assert private.speech.hearing_opens == 0


def test_talk_is_refused_before_accept_when_nobody_is_signed_in() -> None:
    talk, private, _clock = a_copresenter_talk_host(decks=_UnreadDecks())

    assert _hear_with_origin(talk.client, TALK_ORIGIN, TALK) == POLICY_VIOLATION
    assert private.speech.hearing_opens == 0


def test_hear_is_accepted_for_a_user_from_the_talk_origin() -> None:
    """A hearing is accepted for a signed-in user."""
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=Role.USER)

    assert _hear_with_origin(talk.client, TALK_ORIGIN) is None
    assert private.speech.hearing_opens == 1


def test_the_lobbys_session_cookie_does_not_sign_in_at_the_talk_host() -> None:
    talk, private, clock = a_copresenter_talk_host()
    session_id = talk.people.open_session_for(
        User(id="id-of-an-admin", username=USERNAME, role=Role.ADMIN)
    )
    hand_over_session_cookies(talk.client, session_id, a_web_auth(clock))
    assert DEFAULT_SESSION_COOKIE_NAME in talk.client.cookies

    answer = who(talk.client, headers=_JSON)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert private.speech.readiness_calls == 0


def test_a_sign_in_at_the_talk_host_sets_no_csrf_cookie(engine: Engine) -> None:
    """The session cookie is set and no cookie named csrf is."""
    hosts = two_hosts(engine)

    returned = hosts.sign_in(hosts.browser(A_TALK_ADDRESS), A_TALK_ADDRESS)

    cookies = returned.headers.get_list("set-cookie")
    assert any(
        cookie.startswith(f"{DEFAULT_SESSION_COOKIE_NAME}=") for cookie in cookies
    )
    assert not any("csrf" in cookie.lower() for cookie in cookies)


def test_an_admin_demoted_while_signed_in_is_still_answered() -> None:
    """CP4: a role change does not end the answer."""
    talk, private, _clock = a_copresenter_talk_host()
    talk.sign_in(role=Role.ADMIN)
    (admin,) = talk.people.users.users.values()
    talk.people.users.users[admin.id] = replace(admin, role=Role.USER)

    answer = who(talk.client, headers=_JSON)

    assert answer.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


def _talk_host_with_a_signed_in_admin(
    engine: Engine, deadline: ManualDeadline | None = None
) -> tuple[Hosts, TestClient, PrivateFakes]:
    hosts, private = two_hosts_with_a_copresenter(
        engine, asyncio.sleep if deadline is None else deadline.wait
    )
    return hosts, hosts.signed_in_at(A_TALK_ADDRESS), private


def _demote_at_the_lobby(hosts: Hosts) -> None:
    """The admin signs in at the lobby with the issuer's groups no longer admin."""
    lobby = hosts.browser(A_BROWSERS_ADDRESS)
    hosts.sign_in(lobby, A_BROWSERS_ADDRESS, subject=A.id, groups=MEMBER)


# DK11
def test_a_lobby_sign_in_that_demotes_the_admin_keeps_the_deck_and_who(
    engine: Engine,
) -> None:
    """A role change does not end the deck or who."""
    hosts, talk, private = _talk_host_with_a_signed_in_admin(engine)
    _demote_at_the_lobby(hosts)

    deck = talk.get(f"/deck/{A_DECK}/")
    answer = who(talk, headers=_JSON)

    assert deck.status_code == HTTPStatus.OK
    assert answer.status_code == HTTPStatus.OK
    assert private.speech.readiness_calls == 1


def test_a_lobby_sign_in_that_demotes_the_admin_keeps_the_running_hearing(
    engine: Engine,
) -> None:
    """CP4: a role change does not end a running hearing."""
    deadline = ManualDeadline()
    hosts, talk, private = _talk_host_with_a_signed_in_admin(engine, deadline)

    with talk.websocket_connect(HEARING, headers={"origin": TALK_ORIGIN}) as socket:
        assert deadline.started.wait(timeout=1)
        _demote_at_the_lobby(hosts)
        deadline.release_first()
        socket.send_bytes(b"after demotion")
        assert socket.receive_json() == {
            "text": "gehört",
            "final": True,
            "language": "de",
            "traceparent": None,
        }

    assert private.speech.hearing.frames == [b"after demotion"]


@pytest.mark.parametrize(
    ("accept", "status"),
    [
        pytest.param("text/html", HTTPStatus.FOUND, id="page"),
        pytest.param("application/json", HTTPStatus.UNAUTHORIZED, id="json"),
    ],
)
def test_a_signed_out_request_for_a_talk_is_sent_to_sign_in_or_refused(
    engine: Engine, accept: str, status: HTTPStatus
) -> None:
    hosts: Hosts = two_hosts(engine)
    browser = hosts.browser(A_TALK_ADDRESS, headers={"accept": accept})

    answer = browser.get(f"/deck/{A_DECK}/")

    assert answer.status_code == status
    if status == HTTPStatus.FOUND:
        assert answer.headers["location"].startswith("/login/oidc?next=")
