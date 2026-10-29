"""Logging out ends the session and answers the page that ends Keycloak's too."""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime
from html import unescape
from http import HTTPStatus
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from pydantic import SecretStr
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME
from webauth.ports import SessionRecord, SessionRecordStore

from presentator.api.auth import LOGIN_PATH, LOGOUT_PATH
from presentator.host.oidc import oidc_client_config
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_CLIENT_CREDENTIAL,
    a_settings,
)
from tests.support.sign_in_support import Scene, TransactionDouble, a_scene

_A_FORM = {"content-type": "application/x-www-form-urlencoded"}
_END_SESSION = oidc_client_config(
    a_settings(), SecretStr(A_CLIENT_CREDENTIAL), origin=A_BROWSERS_ADDRESS
)
_HIDDEN_FIELD = re.compile(r'<input type="hidden" name="([^"]+)" value="([^"]*)">')


@dataclass
class Breakdown:
    """Whether the disk behind the double has failed yet; the sign-in comes first."""

    now: bool = False


@dataclass(frozen=True)
class SessionsThatBreakOnDelete:
    """The real session store, whose delete raises once the breakdown has begun."""

    real: SessionRecordStore
    breakdown: Breakdown

    def create(
        self,
        user_id: str,
        *,
        now: datetime,
        expires_at: datetime,
        refresh_token: SecretStr | None,
        id_token: SecretStr | None,
    ) -> SessionRecord:
        return self.real.create(
            user_id,
            now=now,
            expires_at=expires_at,
            refresh_token=refresh_token,
            id_token=id_token,
        )

    def load(self, session_id: str) -> SessionRecord | None:
        return self.real.load(session_id)

    def touch(self, session_id: str, *, expires_at: datetime) -> bool:
        return self.real.touch(session_id, expires_at=expires_at)

    def replace_refresh_token(
        self, session_id: str, *, refresh_token: SecretStr
    ) -> bool:
        return self.real.replace_refresh_token(session_id, refresh_token=refresh_token)

    def delete(self, session_id: str) -> None:
        if self.breakdown.now:
            message = "disk I/O error"
            raise sqlite3.OperationalError(message)
        self.real.delete(session_id)

    def delete_for_user(self, user_id: str) -> int:
        return self.real.delete_for_user(user_id)

    def prune_overflow(self, user_id: str, keep_newest: int) -> None:
        self.real.prune_overflow(user_id, keep_newest)


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path)


def _posted_fields(page: str) -> dict[str, str]:
    """The hidden fields of the page's one form, as a browser would post them."""
    return {name: unescape(value) for name, value in _HIDDEN_FIELD.findall(page)}


def _cleared_paths(set_cookie_headers: list[str]) -> dict[str, str]:
    """Each cookie a logout deleted with Max-Age=0, by exact name, to its path."""
    deletions: dict[str, str] = {}
    for header in set_cookie_headers:
        for name, morsel in SimpleCookie(header).items():
            assert morsel["max-age"] == "0"
            assert name not in deletions
            deletions[name] = morsel["path"]
    return deletions


def _directives(policy: str) -> dict[str, str]:
    return dict(part.strip().split(" ", 1) for part in policy.split(";"))


def test_logging_out_answers_the_page_that_posts_the_stored_hint_to_the_issuer(
    scene: Scene,
) -> None:
    """#428 cell 12: the page posts the stored hint and the row is gone."""
    scene.sign_in()
    (stored,) = scene.stored_id_tokens()

    answer = scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    assert answer.status_code == HTTPStatus.OK
    assert "location" not in answer.headers
    assert answer.headers["cache-control"] == "no-store"
    assert f'action="{_END_SESSION.end_session_endpoint}"' in answer.text
    assert _posted_fields(answer.text) == {
        "client_id": _END_SESSION.client_id,
        "post_logout_redirect_uri": _END_SESSION.post_logout_redirect_uri,
        "id_token_hint": stored,
    }
    assert scene.count("sessions") == 0
    assert _cleared_paths(answer.headers.get_list("set-cookie")) == {
        DEFAULT_SESSION_COOKIE_NAME: "/",
    }


def _arrange_no_hint(scene: Scene, arrangement: str) -> None:
    scene.sign_in()
    match arrangement:
        case "no-session-cookie":
            del scene.lobby.client.cookies[DEFAULT_SESSION_COOKIE_NAME]
        case "row-gone":
            _in_the_file(scene, "DELETE FROM sessions")
        case _:
            _in_the_file(scene, "UPDATE sessions SET id_token = NULL")


def _in_the_file(scene: Scene, statement: str) -> None:
    with closing(sqlite3.connect(scene.database)) as connection, connection:
        connection.execute(statement)


@pytest.mark.parametrize("arrangement", ["no-session-cookie", "row-gone", "no-token"])
def test_a_logout_with_no_stored_hint_posts_only_the_client_and_the_return_address(
    scene: Scene, arrangement: str
) -> None:
    """#428 cell 13: with no stored hint the page has no `id_token_hint`."""
    _arrange_no_hint(scene, arrangement)

    answer = scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    assert answer.status_code == HTTPStatus.OK
    assert _posted_fields(answer.text) == {
        "client_id": _END_SESSION.client_id,
        "post_logout_redirect_uri": _END_SESSION.post_logout_redirect_uri,
    }
    assert urlsplit(_END_SESSION.post_logout_redirect_uri).path == LOGIN_PATH


def test_the_logout_page_brings_its_own_policy_naming_the_issuer(
    scene: Scene,
) -> None:
    """#428 cell 14: the page carries webauth's policy naming the issuer."""
    scene.sign_in()

    answer = scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    directives = _directives(answer.headers["content-security-policy"])
    assert directives["script-src"].startswith("'sha256-")
    endpoint = urlsplit(_END_SESSION.end_session_endpoint)
    assert f"{endpoint.scheme}://{endpoint.netloc}" in directives["form-action"]
    assert A_BROWSERS_ADDRESS in directives["form-action"]
    assert "'self'" not in directives["form-action"]


def test_the_fallback_button_reads_the_catalogs_log_out(scene: Scene) -> None:
    """#428 cell 15: the fallback button reads the catalog's `log_out`."""
    scene.sign_in()

    answer = scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    assert '<button type="submit">Log out</button>' in answer.text


def test_the_session_cookie_of_a_logged_out_browser_cannot_come_back(
    scene: Scene,
) -> None:
    scene.sign_in()
    cookie = scene.lobby.client.cookies[DEFAULT_SESSION_COOKIE_NAME]

    scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    scene.lobby.client.cookies.set(DEFAULT_SESSION_COOKIE_NAME, cookie)
    assert scene.lobby.client.get("/").status_code == HTTPStatus.FOUND


def test_a_logout_form_without_a_token_from_this_origin_is_accepted(
    scene: Scene,
) -> None:
    scene.sign_in()

    accepted = scene.lobby.client.post(
        LOGOUT_PATH,
        headers={**_A_FORM, "origin": scene.lobby.own_origin()},
    )

    assert accepted.status_code == HTTPStatus.OK
    assert scene.count("sessions") == 0


def test_a_stale_csrf_cookie_of_the_removed_token_layer_does_not_block_a_logout(
    scene: Scene,
) -> None:
    """#474 cell 27: the removed second cookie is ignored at logout."""
    scene.sign_in()
    scene.lobby.client.cookies.set("__Host-csrf_token", "stale")

    accepted = scene.lobby.client.post(
        LOGOUT_PATH,
        headers={**_A_FORM, "origin": scene.lobby.own_origin()},
    )

    assert accepted.status_code == HTTPStatus.OK
    assert scene.count("sessions") == 0


@pytest.mark.parametrize(
    ("headers", "sends_fetch_site"),
    [
        pytest.param(
            {"origin": "https://another.example", "sec-fetch-site": "cross-site"},
            True,
            id="foreign-origin",
        ),
        pytest.param({"sec-fetch-site": "cross-site"}, True, id="cross-site-fetch"),
        pytest.param(
            {"origin": "https://another.example"},
            False,
            id="foreign-origin-without-fetch-site",
        ),
    ],
)
def test_a_logout_form_another_site_submitted_deletes_nothing_and_goes_nowhere(
    scene: Scene,
    headers: dict[str, str],
    *,
    sends_fetch_site: bool,
) -> None:
    scene.sign_in()
    if not sends_fetch_site:
        del scene.lobby.client.headers["sec-fetch-site"]

    refused = scene.lobby.client.post(LOGOUT_PATH, headers={**_A_FORM, **headers})

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert "location" not in refused.headers
    assert scene.count("sessions") == 1


@pytest.mark.parametrize("signed_in", [True, False], ids=["signed-in", "signed-out"])
def test_a_logout_form_with_no_origin_signal_deletes_nothing_and_goes_nowhere(
    scene: Scene,
    *,
    signed_in: bool,
) -> None:
    if signed_in:
        scene.sign_in()
    del scene.lobby.client.headers["sec-fetch-site"]

    refused = scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert "location" not in refused.headers
    assert scene.count("sessions") == (1 if signed_in else 0)


def test_a_logout_enters_the_transaction_once_on_a_worker_thread(
    tmp_path: Path,
) -> None:
    double = TransactionDouble()
    scene = a_scene(tmp_path, transaction=double.around)
    scene.sign_in()
    double.log.clear()

    scene.lobby.client.post(LOGOUT_PATH, headers=_A_FORM)

    assert [step for step, _ in double.log] == ["entered", "left"]
    assert double.log[0] == ("entered", False)


def test_a_store_failure_in_the_logout_answers_500_clears_no_cookie_and_keeps_the_row(
    tmp_path: Path,
) -> None:
    breakdown = Breakdown()
    double = TransactionDouble(
        through=lambda stores: replace(
            stores, sessions=SessionsThatBreakOnDelete(stores.sessions, breakdown)
        )
    )
    scene = a_scene(tmp_path, transaction=double.around)
    browser = scene.browser(raise_server_exceptions=False)
    scene.sign_in(browser)
    breakdown.now = True

    failed = browser.post(LOGOUT_PATH, headers=_A_FORM)

    assert failed.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert failed.headers.get_list("set-cookie") == []
    assert scene.count("sessions") == 1
    breakdown.now = False
    assert browser.post(LOGOUT_PATH, headers=_A_FORM).status_code == HTTPStatus.OK
    assert scene.count("sessions") == 0


def test_every_other_answer_of_the_lobby_lets_a_form_go_to_this_origin_only(
    scene: Scene,
) -> None:
    """#428 cell 21: every other answer's `form-action` is this origin only."""
    answer = scene.lobby.client.get("/login")

    directives = _directives(answer.headers["content-security-policy"])
    assert directives["form-action"] == "'self'"


def test_a_logout_by_get_is_no_logout(scene: Scene) -> None:
    """#428 cell 18: `GET /logout` is no logout and the row stays."""
    scene.sign_in()

    answer = scene.lobby.client.get(LOGOUT_PATH)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert scene.count("sessions") == 1


def test_the_header_form_and_the_datenschutz_link_name_the_paths_of_the_routes(
    scene: Scene,
) -> None:
    scene.sign_in()
    signed_in_page = scene.lobby.client.get("/").text
    signed_out_page = scene.browser().get("/datenschutz").text

    assert f'action="{LOGOUT_PATH}"' in signed_in_page
    assert f'href="{LOGIN_PATH}"' in signed_out_page
