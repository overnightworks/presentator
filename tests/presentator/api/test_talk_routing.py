"""The Host header alone chooses which app of the instance answers."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from httpx2 import Response
from pydantic import SecretStr
from starlette.testclient import TestClient, WebSocketDenialResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect
from webauth.config import installed_web_auth_config
from webauth.oidc import OidcClientConfig, OidcRelyingParty

from presentator.adapters.identity import SqliteSessionRecordStore
from presentator.api.auth import BACKCHANNEL_LOGOUT_PATH, DEFAULT_BODY_BYTES
from presentator.api.talk import TALK_PERMISSIONS_POLICY, talk_content_security_policy
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.models import Role, User
from presentator.host import main
from presentator.host.config import load_settings
from tests.support.fake_issuer import FakeIssuer
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_TALK_ADDRESS,
    hand_over_session_cookies,
)
from tests.support.people import seed_user
from tests.support.talk_support import (
    A_DECK,
    B_DECK,
    A,
    Hosts,
    ask_at_the_door,
    cookie_header,
    two_hosts,
)

_LOBBY_HOST = str(urlsplit(A_BROWSERS_ADDRESS).hostname)
_TALK_HOST = str(urlsplit(A_TALK_ADDRESS).hostname)
_SOMEONE_ELSES = "elsewhere.example.test"
_NOT_FOUND = HTTPStatus.NOT_FOUND
_JSON = {"accept": "application/json"}
_ADMIN = User(id="id-of-felix", username="felix", role=Role.ADMIN)


@pytest.fixture
def hosts(tmp_path: Path) -> Hosts:
    return two_hosts(tmp_path)


def _is_the_talk_host(permissions_policy: str | None) -> bool:
    return permissions_policy == TALK_PERMISSIONS_POLICY


def test_the_router_gives_each_hostname_its_app_and_any_other_a_not_found(
    hosts: Hosts,
) -> None:
    lobby_login = hosts.browser(A_BROWSERS_ADDRESS).get("/login")
    talk_root = hosts.browser(A_TALK_ADDRESS).get("/")
    stranger = hosts.browser(f"https://{_SOMEONE_ELSES}").get("/login")

    assert lobby_login.status_code == HTTPStatus.OK
    assert talk_root.status_code == HTTPStatus.SEE_OTHER
    assert talk_root.headers["location"] == A_BROWSERS_ADDRESS
    assert stranger.status_code == _NOT_FOUND


def test_a_socket_for_a_hostname_the_instance_does_not_serve_is_refused(
    hosts: Hosts,
) -> None:
    stranger = hosts.browser(f"https://{_SOMEONE_ELSES}")

    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        stranger.websocket_connect(f"wss://{_SOMEONE_ELSES}/deck/{A_DECK}/"),
    ):
        pytest.fail("a socket was accepted")


def test_a_scope_with_no_host_header_is_a_not_found(hosts: Hosts) -> None:
    answer = ask_at_the_door(hosts.router, hosts=(), path="/login")

    assert answer.status == _NOT_FOUND


@pytest.mark.parametrize(
    ("first", "second", "answered_by_talk"),
    [(_TALK_HOST, _LOBBY_HOST, True), (_LOBBY_HOST, _TALK_HOST, False)],
    ids=["talk first", "lobby first"],
)
def test_with_two_host_headers_only_the_first_hosts_app_answers(
    hosts: Hosts,
    first: str,
    second: str,
    answered_by_talk: bool,  # noqa: FBT001
) -> None:
    answer = ask_at_the_door(hosts.router, hosts=(first, second), path="/")

    assert (
        _is_the_talk_host(answer.headers.get("permissions-policy")) is answered_by_talk
    )


def test_a_port_on_the_talk_host_never_reaches_the_lobbys_pages(hosts: Hosts) -> None:
    ported = hosts.browser(f"{A_TALK_ADDRESS}:8443")

    settings = ported.get("/settings")

    assert settings.headers["location"].startswith("/login/oidc?")
    assert _is_the_talk_host(settings.headers["permissions-policy"])


def test_a_port_on_the_lobby_never_gets_a_talk_host_answer(hosts: Hosts) -> None:
    ported = hosts.browser_with_the_cookies_of(
        hosts.signed_in_at(A_BROWSERS_ADDRESS), f"{A_BROWSERS_ADDRESS}:8443"
    )

    page = ported.get(f"/deck/{A_DECK}", headers={"accept": "text/html"})

    assert page.status_code == HTTPStatus.OK
    assert not _is_the_talk_host(page.headers.get("permissions-policy"))


@pytest.mark.parametrize(
    "target",
    [f"{A_BROWSERS_ADDRESS}/settings", "/settings"],
    ids=["absolute-form as the path (h11)", "origin-form (httptools)"],
)
def test_an_absolute_form_target_naming_the_lobby_is_the_talk_hosts_not_found(
    hosts: Hosts,
    target: str,
) -> None:
    client = hosts.signed_in_at(A_TALK_ADDRESS)

    answer = ask_at_the_door(
        hosts.router, hosts=(_TALK_HOST,), path=target, cookie=cookie_header(client)
    )

    assert answer.status == _NOT_FOUND
    assert _is_the_talk_host(answer.headers["permissions-policy"])


@pytest.mark.parametrize("slug", [A_DECK, f"{B_DECK}/presenter"])
@pytest.mark.parametrize("signed_in", [True, False], ids=["with a session", "none"])
def test_a_socket_to_a_talk_is_closed_before_it_is_accepted(
    hosts: Hosts,
    slug: str,
    signed_in: bool,  # noqa: FBT001
) -> None:
    browser = (
        hosts.signed_in_at(A_TALK_ADDRESS)
        if signed_in
        else hosts.browser(A_TALK_ADDRESS)
    )

    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        browser.websocket_connect(f"wss://{_TALK_HOST}/deck/{slug}/"),
    ):
        pytest.fail("a socket was accepted")


@pytest.mark.usefixtures("environment")
def test_the_composition_root_wires_the_talk_host_to_its_own_policy() -> None:
    settings = load_settings()
    router = main.served_by_host(main.build_instance(settings), settings)
    talk = TestClient(router, base_url=settings.talk_origin, follow_redirects=False)

    root = talk.get("/")
    from_the_lobby = talk.post(
        "/logout",
        headers={"origin": settings.public_origin, "sec-fetch-site": "same-site"},
    )
    from_the_talk_host = talk.post("/logout", headers={"origin": settings.talk_origin})

    assert root.status_code == HTTPStatus.SEE_OTHER
    assert root.headers["location"] == settings.public_origin
    assert root.headers["content-security-policy"] == talk_content_security_policy()
    assert root.headers["permissions-policy"] == TALK_PERMISSIONS_POLICY
    assert "microphone=(self)" in TALK_PERMISSIONS_POLICY
    assert from_the_lobby.status_code == HTTPStatus.FORBIDDEN
    assert from_the_talk_host.status_code != HTTPStatus.FORBIDDEN


# CP13
@pytest.mark.usefixtures("environment")
def test_the_composition_root_serves_the_copresenter_at_the_talk_host_only() -> None:
    settings = load_settings()
    instance = main.build_instance(settings)
    router = main.served_by_host(instance, settings)
    seed_user(settings.database, _ADMIN)
    now = datetime.now(UTC)
    session = SqliteSessionRecordStore(settings.database).create(
        _ADMIN.id,
        now=now,
        expires_at=now + SESSION_LIFETIME,
        refresh_token=None,
        id_token=SecretStr("an-id-token"),
        account_language=None,
    )
    lobby = TestClient(router, base_url=settings.public_origin)
    talk = TestClient(router, base_url=settings.talk_origin)
    hand_over_session_cookies(
        lobby, session.id, installed_web_auth_config(instance.lobby)
    )
    hand_over_session_cookies(
        talk, session.id, installed_web_auth_config(instance.talk)
    )
    hearing = f"wss://{_LOBBY_HOST}/copresenter/hear?language=de"

    at_the_lobby = lobby.get("/copresenter/who", headers=_JSON)
    at_the_talk_host = talk.get("/copresenter/who", headers=_JSON)
    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        lobby.websocket_connect(hearing, headers={"origin": settings.public_origin}),
    ):
        pytest.fail("the lobby accepted a hearing")

    assert at_the_lobby.status_code == _NOT_FOUND
    assert at_the_talk_host.status_code == _NOT_FOUND
    assert at_the_lobby.json() == lobby.get("/no-such-route", headers=_JSON).json()
    assert at_the_talk_host.json() != at_the_lobby.json()


@pytest.mark.usefixtures("environment")
def test_the_talk_hosts_sign_in_stores_no_id_token_while_the_lobbys_does() -> None:
    """The lobby row keeps the ID token, talk row none."""
    settings = load_settings()
    now = datetime.now(UTC)
    issuer = FakeIssuer(
        issuer=settings.oidc_issuer, client_id=settings.oidc_client_id, now=now
    )

    def relying_party_over_the_fake_issuer(
        config: OidcClientConfig,
    ) -> OidcRelyingParty:
        return OidcRelyingParty(
            config,
            post_token_request=issuer.post_token_request,
            fetch_key_set=issuer.fetch_key_set,
            wall_clock=now.timestamp,
            monotonic_clock=lambda: 0.0,
        )

    router = main.served_by_host(
        main.build_instance(settings, relying_party_over_the_fake_issuer), settings
    )

    def stored_id_tokens() -> list[str | None]:
        with closing(sqlite3.connect(settings.database)) as connection:
            return [
                row[0] for row in connection.execute("SELECT id_token FROM sessions")
            ]

    def sign_in_at(origin: str) -> None:
        browser = TestClient(
            router,
            base_url=origin,
            follow_redirects=False,
            headers=A_BROWSERS_HEADERS,
        )
        started = browser.get("/login/oidc").headers["location"]
        query = issuer.authorize(
            started, subject="subject-1", groups=["/apps/presentator"], username="felix"
        )
        assert (
            browser.get(f"/auth/callback?{query}").status_code == HTTPStatus.SEE_OTHER
        )

    sign_in_at(settings.talk_origin)
    assert stored_id_tokens() == [None]

    sign_in_at(settings.public_origin)
    assert sorted(token is None for token in stored_id_tokens()) == [False, True]


# ---- the back-channel logout under a foreign Host ----------------------------

_INTERNAL_NAME = "http://web.presentator.svc:8000"
_FOREIGN_ADDRESSES = [
    pytest.param(_INTERNAL_NAME, id="service name with port"),
    pytest.param("http://web.presentator.svc", id="service name"),
    pytest.param("http://10.43.7.21:8000", id="ip with port"),
    pytest.param(None, id="no host header"),
]
_FORM = {"content-type": "application/x-www-form-urlencoded"}


def _without_host_header(app: ASGIApp) -> ASGIApp:
    async def asgi(scope: Scope, receive: Receive, send: Send) -> None:
        headers = [(name, value) for name, value in scope["headers"] if name != b"host"]
        await app({**scope, "headers": headers}, receive, send)

    return asgi


def _caller(hosts: Hosts, address: str | None, **headers: str) -> TestClient:
    """A server that sends no cookie and no Origin, at this address or with no Host."""
    return TestClient(
        hosts.router if address else _without_host_header(hosts.router),
        base_url=address or _INTERNAL_NAME,
        follow_redirects=False,
        headers=headers,
    )


def _send(
    client: TestClient,
    method: str,
    path: str,
    body: str | bytes,
    **headers: str,
) -> Response:
    return client.request(method, path, content=body, headers={**_FORM, **headers})


def _logout_token_form(token: str) -> str:
    return urlencode({"logout_token": token})


def _signed_in_twice(hosts: Hosts) -> None:
    hosts.sign_in(hosts.browser(A_BROWSERS_ADDRESS), A_BROWSERS_ADDRESS)
    hosts.sign_in(hosts.browser(A_TALK_ADDRESS), A_TALK_ADDRESS)
    assert hosts.session_count() == 2  # noqa: PLR2004


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
def test_a_valid_logout_token_under_a_foreign_host_ends_the_subjects_sessions(
    hosts: Hosts, address: str | None
) -> None:
    _signed_in_twice(hosts)

    ended = _send(
        _caller(hosts, address),
        "POST",
        BACKCHANNEL_LOGOUT_PATH,
        _logout_token_form(hosts.issuer.mint_logout_token(subject="subject-1")),
    )

    assert ended.status_code == HTTPStatus.OK
    assert hosts.session_count() == 0


_REFUSED_TOKENS: dict[str, Callable[[FakeIssuer], str]] = {
    "another key": lambda issuer: issuer.mint_logout_token(
        subject="subject-1", by_another_key=True
    ),
    "too old": lambda issuer: issuer.mint_logout_token(
        subject="subject-1", seconds_ago=3600
    ),
    "no token": lambda _: "",
}


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
@pytest.mark.parametrize("token", _REFUSED_TOKENS.values(), ids=_REFUSED_TOKENS.keys())
def test_an_invalid_logout_token_under_a_foreign_host_is_refused_as_at_the_lobby(
    hosts: Hosts, address: str | None, token: Callable[[FakeIssuer], str]
) -> None:
    _signed_in_twice(hosts)
    form = _logout_token_form(token(hosts.issuer))

    at_the_lobby = _send(
        hosts.browser(A_BROWSERS_ADDRESS), "POST", BACKCHANNEL_LOGOUT_PATH, form
    )
    refused = _send(_caller(hosts, address), "POST", BACKCHANNEL_LOGOUT_PATH, form)

    assert refused.status_code == at_the_lobby.status_code
    assert refused.status_code != HTTPStatus.OK
    assert hosts.session_count() == 2  # noqa: PLR2004


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "DELETE"])
def test_any_method_but_post_on_the_logout_path_under_a_foreign_host_is_not_allowed(
    hosts: Hosts, address: str | None, method: str
) -> None:
    _signed_in_twice(hosts)

    answer = _caller(hosts, address).request(method, BACKCHANNEL_LOGOUT_PATH)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert "location" not in answer.headers
    assert "set-cookie" not in answer.headers
    assert hosts.session_count() == 2  # noqa: PLR2004


_NOT_THE_LOGOUT_PATH = [
    pytest.param(f"{BACKCHANNEL_LOGOUT_PATH}/", id="trailing slash"),
    pytest.param(f"{BACKCHANNEL_LOGOUT_PATH}-again", id="longer name"),
    pytest.param(f"{BACKCHANNEL_LOGOUT_PATH}/more", id="below it"),
    pytest.param(BACKCHANNEL_LOGOUT_PATH.rsplit("/", 1)[0], id="its parent"),
    pytest.param("/", id="lobby root"),
    pytest.param("/login", id="lobby page"),
    pytest.param("/logout", id="lobby form"),
    pytest.param("/api/sources", id="lobby api"),
    pytest.param(f"/deck/{A_DECK}/", id="talk deck"),
    pytest.param(f"/deck/{A_DECK}/presenter/", id="talk presenter"),
    pytest.param("/copresenter/who", id="talk copresenter"),
]


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("path", _NOT_THE_LOGOUT_PATH)
def test_every_other_path_under_a_foreign_host_is_a_not_found_without_a_redirect(
    hosts: Hosts, address: str | None, method: str, path: str
) -> None:
    _signed_in_twice(hosts)

    answer = _send(
        _caller(hosts, address),
        method,
        path,
        _logout_token_form(hosts.issuer.mint_logout_token(subject="subject-1")),
    )

    assert answer.status_code == _NOT_FOUND
    assert "location" not in answer.headers
    assert "set-cookie" not in answer.headers
    assert hosts.session_count() == 2  # noqa: PLR2004


@pytest.mark.parametrize(
    ("signed_in", "status"),
    [(True, _NOT_FOUND), (False, HTTPStatus.FOUND)],
    ids=["with a session", "none"],
)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_the_talk_host_never_serves_the_logout_path_and_keeps_its_session(
    hosts: Hosts,
    signed_in: bool,  # noqa: FBT001
    status: int,
    method: str,
) -> None:
    talk = hosts.browser(A_TALK_ADDRESS)
    if signed_in:
        hosts.sign_in(talk, A_TALK_ADDRESS)
    hosts.sign_in(hosts.browser(A_BROWSERS_ADDRESS), A_BROWSERS_ADDRESS)
    hosts.give_a_deck_to(A.username)
    presenter = f"/deck/{A_DECK}/presenter/"
    standing = talk.get(presenter).status_code
    sessions = hosts.session_count()

    answer = _send(
        talk,
        method,
        BACKCHANNEL_LOGOUT_PATH,
        _logout_token_form(hosts.issuer.mint_logout_token(subject="subject-1")),
        **{"origin": A_TALK_ADDRESS, "sec-fetch-site": "same-origin"},
    )

    assert answer.status_code == status
    assert hosts.session_count() == sessions
    assert talk.get(presenter).status_code == standing


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
def test_a_body_over_the_cap_under_a_foreign_host_is_refused_as_at_the_lobby(
    hosts: Hosts, address: str | None
) -> None:
    oversized = b"x" * (DEFAULT_BODY_BYTES + 1)

    at_the_lobby = _send(
        hosts.browser(A_BROWSERS_ADDRESS), "POST", BACKCHANNEL_LOGOUT_PATH, oversized
    )
    refused = _send(_caller(hosts, address), "POST", BACKCHANNEL_LOGOUT_PATH, oversized)

    assert refused.status_code == at_the_lobby.status_code
    assert refused.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


@pytest.mark.parametrize(
    "forwarded",
    [
        {"x-forwarded-for": "203.0.113.9"},
        {"x-forwarded-host": _LOBBY_HOST},
        {"x-forwarded-host": _TALK_HOST},
        {"x-forwarded-proto": "https", "x-forwarded-port": "443"},
    ],
    ids=["for", "host of the lobby", "host of the talk host", "proto and port"],
)
def test_forwarded_headers_change_nothing_about_the_logout_under_a_foreign_host(
    hosts: Hosts, forwarded: dict[str, str]
) -> None:
    _signed_in_twice(hosts)
    caller = _caller(hosts, _INTERNAL_NAME, **forwarded)

    elsewhere = caller.get("/login")
    ended = _send(
        caller,
        "POST",
        BACKCHANNEL_LOGOUT_PATH,
        _logout_token_form(hosts.issuer.mint_logout_token(subject="subject-1")),
    )

    assert elsewhere.status_code == _NOT_FOUND
    assert ended.status_code == HTTPStatus.OK
    assert hosts.session_count() == 0


@pytest.mark.parametrize("address", _FOREIGN_ADDRESSES)
def test_a_socket_to_the_logout_path_under_a_foreign_host_is_refused(
    hosts: Hosts, address: str | None
) -> None:
    caller = _caller(hosts, address)

    with (
        pytest.raises((WebSocketDisconnect, WebSocketDenialResponse)),
        caller.websocket_connect(BACKCHANNEL_LOGOUT_PATH),
    ):
        pytest.fail("a socket was accepted")
