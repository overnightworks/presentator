"""`GET /api/session` tells a page who reads, in which words and theme."""

from dataclasses import replace
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.api.auth import LOGOUT_PATH, START_SIGN_IN_PATH
from presentator.api.preferences import ACCOUNT
from presentator.api.session_api import SessionView
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.preferences import ThemeChoice
from presentator.contracts.text import Catalogs
from tests.presentator.application.support.fakes import FakePersonPreferencesStore
from tests.support.lobby import (
    A_TALK_ADDRESS,
    ENGLISH,
    NEIGHBOUR,
    USERNAME,
    Lobby,
    a_lobby,
    a_lobby_that_claims_no_origin_of_its_own,
)
from tests.support.sign_in_support import ADMIN, MEMBER, Scene, a_scene

_SESSION = "/api/session"
_JSON = {"accept": "application/json"}
_GERMAN = replace(
    ENGLISH, language_tag="de", language_name="Deutsch", log_out="Abmelden"
)
_HAL = "hal-client"
_SUBJECT = "keycloak-subject-of-a-member"


def a_german_lobby() -> Lobby:
    catalogs = Catalogs(by_tag={ENGLISH.language_tag: ENGLISH, "de": _GERMAN})
    return a_lobby(catalogs=catalogs)


def choose(lobby: Lobby, *, language: str, theme: ThemeChoice) -> None:
    lobby.client.post(ACCOUNT, data={"language": language, "theme": theme.value})


def test_a_signed_out_caller_gets_the_instances_words_and_theme_and_no_person() -> None:
    lobby = a_lobby()

    answer = lobby.client.get(_SESSION)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    view = SessionView.model_validate(answer.json())
    assert view.person is None
    assert view.theme == ThemeChoice.FOLLOW_SYSTEM
    assert view.words.log_out == ENGLISH.log_out
    assert view.sign_in.start == START_SIGN_IN_PATH
    assert view.sign_in.logout == LOGOUT_PATH
    assert view.sign_in.talk_start == f"{A_TALK_ADDRESS}{START_SIGN_IN_PATH}"


def test_a_users_own_language_and_theme_win_over_the_instances() -> None:
    lobby = a_german_lobby()
    lobby.sign_in(username=NEIGHBOUR)
    choose(lobby, language="de", theme=ThemeChoice.DARK)

    body = lobby.client.get(_SESSION).json()

    assert body["person"] == {"username": NEIGHBOUR, "role": "user"}
    assert body["theme"] == "dark"
    assert body["words"]["log_out"] == _GERMAN.log_out


def test_an_admin_is_told_apart_by_role() -> None:
    lobby = a_lobby()
    lobby.sign_in()

    assert lobby.client.get(_SESSION).json()["person"] == {
        "username": USERNAME,
        "role": "admin",
    }


@pytest.mark.parametrize("cookie", ["forged", "ended", "expired"])
def test_an_unknown_ended_or_expired_session_is_nobody_not_an_error(
    cookie: str,
) -> None:
    lobby = a_lobby()
    lobby.sign_in()
    if cookie == "ended":
        lobby.people.sessions.rows.clear()
    elif cookie == "expired":
        lobby.clock.advance(SESSION_LIFETIME + timedelta(seconds=1))
    else:
        lobby.client.cookies.set(DEFAULT_SESSION_COOKIE_NAME, cookie)

    answer = lobby.client.get(_SESSION)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["person"] is None


def test_a_preference_store_that_fails_is_an_error_never_a_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lobby = a_lobby()
    lobby.sign_in()

    def unreadable(_store: object, _user_id: str) -> None:
        message = "preference store unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr(FakePersonPreferencesStore, "read", unreadable)
    quiet = TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        follow_redirects=False,
        headers=dict(lobby.client.headers),
        raise_server_exceptions=False,
    )
    quiet.cookies.update(lobby.client.cookies)

    answer = quiet.get(_SESSION)

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


def test_a_post_without_provenance_is_refused_by_the_origin_check() -> None:
    lobby = a_lobby_that_claims_no_origin_of_its_own()

    answer = lobby.client.post(_SESSION)

    assert answer.status_code == HTTPStatus.FORBIDDEN


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({_HAL}))


def a_bearer(scene: Scene, *, subject: str, groups: list[str]) -> dict[str, str]:
    minted = scene.issuer.mint_access_token(
        subject=subject, groups=groups, authorized_party=_HAL, audience=None
    )
    return {"authorization": f"Bearer {minted}", **_JSON}


@pytest.mark.parametrize(
    ("groups", "role"), [(MEMBER, "user"), (ADMIN, "admin")], ids=["user", "admin"]
)
def test_a_persons_bearer_alone_is_that_person_with_the_role_in_the_token(
    scene: Scene, groups: list[str], role: str
) -> None:
    program = scene.browser()

    answer = program.get(
        _SESSION, headers=a_bearer(scene, subject=_SUBJECT, groups=groups)
    )

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["person"]["role"] == role


def test_a_bearer_wins_over_another_persons_cookie_and_keeps_its_own_overrides(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, subject="the-cookies-person", username="cookie-person")
    owner = scene.browser()
    scene.sign_in(owner, subject=_SUBJECT, username="token-person")
    owner.post(ACCOUNT, data={"language": "en", "theme": "dark"})

    answer = browser.get(
        _SESSION, headers=a_bearer(scene, subject=_SUBJECT, groups=MEMBER)
    )

    body = answer.json()
    assert body["person"]["username"] == "token-person"
    assert body["theme"] == "dark"


def test_an_invalid_bearer_beside_a_valid_cookie_is_refused_not_the_cookies_person(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser)

    answer = browser.get(
        _SESSION, headers={"authorization": "Bearer nonsense", **_JSON}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
