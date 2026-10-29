"""The lobby's real routes, driven the way a browser drives them."""

import re
from datetime import timedelta
from http import HTTPStatus

import pytest
from fastapi import Response
from fastapi.testclient import TestClient
from httpx2 import Response as BrowserAnswer
from webauth.cookies import (
    DEFAULT_SESSION_COOKIE_NAME,
    MAX_SESSION_COOKIE_CHARS,
)
from webauth.middleware.security_headers import (
    CONTENT_TYPE_OPTIONS,
    FRAME_OPTIONS,
    REFERRER_POLICY,
    STRICT_TRANSPORT_SECURITY,
)

from presentator.api.hooks import hook_address
from presentator.application.identity import (
    SESSION_LIFETIME,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    ENGLISH,
    USERNAME,
    Lobby,
    a_content_security_policy,
    a_lobby_app,
    a_lobby_that_claims_no_origin_of_its_own,
)

_HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# The lobby's own policy (`presentator.api.auth`): no camera, no microphone,
# never a page's own location.
_NO_DEVICE_PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=()"
# Every one of these answers the same way whatever carried the request: a
# page, a static file, a 404 nothing names, or an exception nothing caught.
_EVERY_SECURITY_HEADER = {
    "cache-control": "no-store",
    "x-content-type-options": CONTENT_TYPE_OPTIONS,
    "x-frame-options": FRAME_OPTIONS,
    "content-security-policy": a_content_security_policy(),
    "referrer-policy": REFERRER_POLICY,
    "permissions-policy": _NO_DEVICE_PERMISSIONS_POLICY,
}


def test_the_lobby_sends_a_visitor_who_is_not_signed_in_to_the_login(
    lobby: Lobby,
) -> None:
    answer = lobby.client.get("/")

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"] == "/login/oidc?next=%2F"


def test_a_signed_out_browser_keeps_its_requested_path_and_query_at_login(
    lobby: Lobby,
) -> None:
    asked = lobby.client.get("/settings/sources?tab=recent")

    assert asked.status_code == HTTPStatus.FOUND
    assert (
        asked.headers["location"]
        == "/login/oidc?next=%2Fsettings%2Fsources%3Ftab%3Drecent"
    )


@pytest.mark.parametrize(
    ("accept", "status"),
    [
        pytest.param("Text/HTML", HTTPStatus.FOUND, id="html in other case"),
        pytest.param("application/json", HTTPStatus.UNAUTHORIZED, id="json"),
        pytest.param(
            "text/html;Q=0, application/json;q=0.5",
            HTTPStatus.UNAUTHORIZED,
            id="html refused by an uppercase Q",
        ),
    ],
)
def test_a_signed_out_request_is_redirected_or_refused_by_what_it_accepts(
    lobby: Lobby, accept: str, status: HTTPStatus
) -> None:
    answer = lobby.client.get(
        "/settings/sources?tab=recent", headers={"accept": accept}
    )

    assert answer.status_code == status
    assert (answer.headers.get("location") is not None) is (status == HTTPStatus.FOUND)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "claims",
    [
        pytest.param({"origin": "https://another.example"}, id="a foreign origin"),
        pytest.param({}, id="no provenance"),
        pytest.param({"referer": f"{A_BROWSERS_ADDRESS}/page"}, id="referer only"),
        pytest.param(
            {"origin": A_BROWSERS_ADDRESS.replace("https://", "http://")},
            id="the host over http",
        ),
        pytest.param(
            {"origin": A_BROWSERS_ADDRESS.replace("https://", "https://evil.")},
            id="a subdomain of it",
        ),
        pytest.param({"origin": f"{A_BROWSERS_ADDRESS}.evil"}, id="it as a prefix"),
    ],
)
def test_an_unlisted_mutating_route_refuses_a_request_without_own_provenance(
    method: str, claims: dict[str, str]
) -> None:
    """#474 cell 4: a route added later is guarded for every mutating method."""
    lobby, _clock = a_lobby_app()

    def a_new_write() -> Response:
        return Response(status_code=HTTPStatus.NO_CONTENT)

    lobby.add_api_route("/a-new-write", a_new_write, methods=[method])
    refused = TestClient(lobby).request(method, "/a-new-write", headers=claims)

    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_a_path_only_resembling_the_hook_prefix_is_not_exempt_from_the_origin_check(
    signed_in_lobby: Lobby,
) -> None:
    """#474 cell 14: only `/sources/` is a server call, never `/sourcesx/`."""
    del signed_in_lobby.client.headers["sec-fetch-site"]

    refused = signed_in_lobby.client.post("/sourcesx/talks/fetch")

    assert refused.status_code == HTTPStatus.FORBIDDEN


def test_a_json_post_with_a_session_and_no_provenance_is_refused(
    signed_in_lobby: Lobby,
) -> None:
    """#474 cell 2: a JSON POST with a session and neither header is refused."""
    del signed_in_lobby.client.headers["sec-fetch-site"]

    refused = signed_in_lobby.client.post("/theme", json={"theme": "dark"})

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert refused.json() == {"detail": "Missing Origin header"}


def submit_the_logout_form(
    lobby: Lobby,
    headers: dict[str, str],
) -> BrowserAnswer:
    """The one form every signed-in page carries, with the claims a test names."""
    return lobby.client.post(
        "/logout",
        headers={"content-type": "application/x-www-form-urlencoded", **headers},
    )


def test_a_seeded_session_opens_the_lobby(lobby: Lobby) -> None:
    lobby.sign_in()

    opened = lobby.client.get("/")

    assert opened.status_code == HTTPStatus.OK
    assert USERNAME in opened.text


def test_an_ordinary_signed_in_answer_sets_no_cookie(signed_in_lobby: Lobby) -> None:
    answer = signed_in_lobby.client.get("/")

    assert answer.headers.get_list("set-cookie") == []


@pytest.mark.parametrize(
    "unusable_cookie",
    [
        pytest.param("doubled", id="doubled"),
        pytest.param("forged", id="forged-signature"),
        pytest.param("oversized", id="oversized"),
    ],
)
def test_a_cookie_the_library_cannot_read_leads_to_the_login_without_a_store_read(
    signed_in_lobby: Lobby,
    unusable_cookie: str,
) -> None:
    """A sibling subdomain can plant a second cookie under the session's name.

    Two same-named cookies are refused rather than one of them picked, and a
    forged or oversized one never reaches the session store.
    """
    genuine = signed_in_lobby.client.cookies[DEFAULT_SESSION_COOKIE_NAME]
    named = f"{DEFAULT_SESSION_COOKIE_NAME}={genuine}"
    cookie = {
        "doubled": f"{named}; {named}",
        "forged": f"{DEFAULT_SESSION_COOKIE_NAME}={genuine[:-1]}x",
        "oversized": f"{DEFAULT_SESSION_COOKIE_NAME}={'a' * MAX_SESSION_COOKIE_CHARS}b",
    }[unusable_cookie]

    answer = signed_in_lobby.client.get("/", headers={"cookie": cookie})

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"] == "/login/oidc?next=%2F"
    assert signed_in_lobby.people.sessions.loads == 0


@pytest.mark.parametrize("method", ["get", "post"])
def test_there_is_no_first_start_page_to_set_up_an_admin(
    signed_in_lobby: Lobby,
    method: str,
) -> None:
    answer = signed_in_lobby.client.request(method, "/setup")

    assert answer.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize("method", ["get", "post"])
def test_there_is_no_first_start_page_for_a_signed_out_reader_either(
    lobby: Lobby,
    method: str,
) -> None:
    answer = lobby.client.request(method, "/setup")

    assert answer.status_code == HTTPStatus.NOT_FOUND


def test_the_login_page_offers_no_form_and_no_password_field(lobby: Lobby) -> None:
    page = lobby.client.get("/login").text

    assert ENGLISH.logged_out_title in page
    assert "<form" not in page
    assert 'type="password"' not in page


def test_a_password_posted_to_the_login_signs_nobody_in(lobby: Lobby) -> None:
    refused = lobby.client.post(
        "/login",
        data={"username": USERNAME, "password": "the words only this test types"},
    )

    assert refused.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert DEFAULT_SESSION_COOKIE_NAME not in lobby.client.cookies


def test_a_login_survives_a_ninety_minute_talk(signed_in_lobby: Lobby) -> None:
    for _ in range(6):
        signed_in_lobby.clock.advance(timedelta(minutes=15))

        assert signed_in_lobby.client.get("/").status_code == HTTPStatus.OK


def test_a_session_left_alone_for_twelve_hours_still_stands_at_the_cap(
    signed_in_lobby: Lobby,
) -> None:
    signed_in_lobby.clock.advance(SESSION_LIFETIME)

    assert signed_in_lobby.client.get("/").status_code == HTTPStatus.OK


def test_a_session_left_alone_for_twelve_hours_leads_back_to_the_login(
    signed_in_lobby: Lobby,
) -> None:
    signed_in_lobby.clock.advance(SESSION_LIFETIME + timedelta(seconds=1))

    assert signed_in_lobby.client.get("/").status_code == HTTPStatus.FOUND


def use_every_quarter_hour_until(lobby: Lobby, lifetime: timedelta) -> None:
    for _ in range(lifetime // timedelta(minutes=15)):
        lobby.clock.advance(timedelta(minutes=15))
        assert lobby.client.get("/").status_code == HTTPStatus.OK


def test_a_session_in_use_all_day_still_stands_at_the_cap(
    signed_in_lobby: Lobby,
) -> None:
    use_every_quarter_hour_until(signed_in_lobby, SESSION_LIFETIME)

    assert signed_in_lobby.client.get("/").status_code == HTTPStatus.OK


def test_a_session_in_use_all_day_is_refused_and_not_renewed_just_past_the_cap(
    signed_in_lobby: Lobby,
) -> None:
    use_every_quarter_hour_until(
        signed_in_lobby, SESSION_LIFETIME - timedelta(minutes=15)
    )
    signed_in_lobby.clock.advance(timedelta(minutes=15, seconds=1))
    touches_before = signed_in_lobby.people.sessions.touches

    assert signed_in_lobby.client.get("/").status_code == HTTPStatus.FOUND
    assert signed_in_lobby.people.sessions.touches == touches_before


def test_every_answer_from_the_lobby_refuses_the_browser_cache(
    signed_in_lobby: Lobby,
) -> None:
    assert signed_in_lobby.client.get("/").headers["cache-control"] == "no-store"
    assert signed_in_lobby.client.get("/login").headers["cache-control"] == "no-store"


def test_a_lobby_reached_over_https_sends_strict_transport_security(
    signed_in_lobby: Lobby,
) -> None:
    answer = signed_in_lobby.client.get("/")

    assert answer.headers["strict-transport-security"] == STRICT_TRANSPORT_SECURITY


def test_the_lobbys_permissions_policy_admits_no_device(
    signed_in_lobby: Lobby,
) -> None:
    answer = signed_in_lobby.client.get("/")

    assert answer.headers["permissions-policy"] == _NO_DEVICE_PERMISSIONS_POLICY


@pytest.mark.parametrize(
    "path",
    ["/", "/static/lobby.css", "/an/address/nothing/names"],
    ids=["a signed-in page", "a static file", "a handled 404"],
)
def test_every_real_answer_carries_the_full_set_of_security_headers(
    signed_in_lobby: Lobby,
    path: str,
) -> None:
    answer = signed_in_lobby.client.get(path)

    for name, value in _EVERY_SECURITY_HEADER.items():
        assert answer.headers[name] == value


def test_an_uncaught_error_still_carries_the_full_set_of_security_headers(
    signed_in_lobby: Lobby,
) -> None:
    def it_breaks() -> Response:
        message = "an error nothing in this route catches"
        raise RuntimeError(message)

    signed_in_lobby.app.add_api_route("/it-breaks", it_breaks, methods=["GET"])
    client = TestClient(
        signed_in_lobby.app,
        base_url=A_BROWSERS_ADDRESS,
        follow_redirects=False,
        headers=A_BROWSERS_HEADERS,
        raise_server_exceptions=False,
    )
    client.cookies.update(signed_in_lobby.client.cookies)

    answer = client.get("/it-breaks")

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    for name, value in _EVERY_SECURITY_HEADER.items():
        assert answer.headers[name] == value


def test_an_address_the_lobby_does_not_know_still_leads_to_the_login(
    lobby: Lobby,
) -> None:
    answer = lobby.client.get("/deck/knowledge-fabric")

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"] == "/login/oidc?next=%2Fdeck%2Fknowledge-fabric"


def test_reading_the_hook_address_leads_to_the_login(lobby: Lobby) -> None:
    asked = lobby.client.get(hook_address("talks"))

    assert asked.status_code == HTTPStatus.FOUND
    assert asked.headers["location"] == "/login/oidc?next=%2Fsources%2Ftalks%2Ffetch"


def test_a_form_naming_the_public_host_is_accepted_without_a_fetch_site() -> None:
    lobby = a_lobby_that_claims_no_origin_of_its_own()

    accepted = submit_the_logout_form(lobby, {"origin": lobby.own_origin()})

    assert accepted.status_code == HTTPStatus.OK


def test_a_navigation_without_an_origin_from_this_instance_is_accepted(
    lobby: Lobby,
) -> None:
    accepted = submit_the_logout_form(lobby, {"sec-fetch-site": "same-origin"})

    assert accepted.status_code == HTTPStatus.OK


@pytest.mark.parametrize("fetch_site", ["same-site", "none"])
def test_a_same_site_or_schemeless_form_post_is_refused(
    signed_in_lobby: Lobby,
    fetch_site: str,
) -> None:
    refused = submit_the_logout_form(
        signed_in_lobby,
        {"sec-fetch-site": fetch_site},
    )

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert signed_in_lobby.client.get("/").status_code == HTTPStatus.OK


def test_the_three_stylesheets_are_linked_on_the_open_login_page(
    lobby: Lobby,
) -> None:
    page = lobby.client.get("/login").text

    assert '<link rel="stylesheet" href="/static/pico.classless.min.css">' in page
    assert '<link rel="stylesheet" href="/static/ui-console.css">' in page
    assert '<link rel="stylesheet" href="/static/lobby.css">' in page


def test_the_three_stylesheets_are_linked_on_the_signed_in_home_page(
    signed_in_lobby: Lobby,
) -> None:
    page = signed_in_lobby.client.get("/").text

    assert '<link rel="stylesheet" href="/static/pico.classless.min.css">' in page
    assert '<link rel="stylesheet" href="/static/ui-console.css">' in page
    assert '<link rel="stylesheet" href="/static/lobby.css">' in page


def test_the_theme_files_are_served_without_signing_in(lobby: Lobby) -> None:
    console = lobby.client.get("/static/ui-console.css")
    leftover = lobby.client.get("/static/lobby.css")
    pico = lobby.client.get("/static/pico.classless.min.css")

    assert console.status_code == HTTPStatus.OK
    assert "--canvas:" in console.text
    assert "@media (prefers-color-scheme: dark)" in console.text
    assert leftover.status_code == HTTPStatus.OK
    assert "--pico-background-color:" in leftover.text
    assert pico.status_code == HTTPStatus.OK
    assert "Pico CSS" in pico.text


def test_an_address_that_only_starts_like_a_stylesheet_still_asks_for_the_login(
    lobby: Lobby,
) -> None:
    walked_out = lobby.client.get("/static/%2e%2e/deck/a-deck/")

    assert walked_out.status_code == HTTPStatus.FOUND
    assert walked_out.headers["location"] == "/login/oidc?next=%2F"


def test_no_signed_in_page_carries_a_hex_colour_or_an_inline_style(
    signed_in_lobby: Lobby,
) -> None:
    for path in ("/", "/login"):
        page = signed_in_lobby.client.get(path).text

        assert not _HEX_COLOUR.search(page)
        assert "style=" not in page


def test_the_signed_in_header_offers_the_person_and_log_out(
    signed_in_lobby: Lobby,
) -> None:
    page = signed_in_lobby.client.get("/").text

    assert USERNAME in page
    assert ENGLISH.log_out in page


def test_a_signed_out_page_carries_only_the_wordmark(lobby: Lobby) -> None:
    page = lobby.client.get("/login").text

    assert ENGLISH.wordmark in page
    assert ENGLISH.log_out not in page
    assert USERNAME not in page


def _links_the_impressum_and_the_datenschutz(page: str) -> bool:
    """Both footer links, the Impressum pointing at the operator's one page."""
    return (
        f'<a href="https://hallucinai.de/impressum">{ENGLISH.footer_impressum}</a>'
        in page
        and f'<a href="https://hallucinai.de/datenschutz#presentator">{ENGLISH.footer_datenschutz}</a>'
        in page
    )


@pytest.mark.parametrize("path", ["/login", "/login/talk/no-access"])
def test_a_signed_out_page_links_the_impressum_and_the_datenschutz(
    lobby: Lobby,
    path: str,
) -> None:
    assert _links_the_impressum_and_the_datenschutz(lobby.client.get(path).text)


@pytest.mark.parametrize("path", ["/", "/account"])
def test_a_signed_in_page_links_the_impressum_and_the_datenschutz(
    signed_in_lobby: Lobby,
    path: str,
) -> None:
    page = signed_in_lobby.client.get(path).text

    assert ENGLISH.log_out in page
    assert _links_the_impressum_and_the_datenschutz(page)
