"""`GET /api/session` tells a page who reads, in which language and theme."""

from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.api.auth import LOGOUT_PATH, START_SIGN_IN_PATH
from presentator.api.session_api import SessionView
from presentator.application.identity import SESSION_LIFETIME
from presentator.contracts.models import Role, User
from presentator.contracts.preferences import (
    DEFAULT_LANGUAGE,
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from tests.presentator.application.support.fakes import FakePersonPreferencesStore
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_TALK_ADDRESS,
    AN_ISSUER,
    NEIGHBOUR,
    USERNAME,
    Lobby,
    a_browser_at,
    a_crowd,
    a_lobby,
    a_lobby_app,
    a_lobby_that_claims_no_origin_of_its_own,
    a_preferences,
    hand_over_session_cookies,
)
from tests.support.sign_in_support import ADMIN, MEMBER, Scene, a_scene

_SESSION = "/api/session"
_JSON = {"accept": "application/json"}
_HAL = "hal-client"
_SUBJECT = "keycloak-subject-of-a-member"


def a_lobby_speaking(
    *, instance: str, account: str | None = None, signed_in: bool = True
) -> Lobby:
    """A lobby whose instance stores `instance`, with its reader's account language."""
    preferences = a_preferences()
    preferences.instance.write(InstanceSettings(language_tag=instance))
    people = a_crowd()
    app, _ = a_lobby_app(people=people, preferences=preferences)
    lobby = a_browser_at(
        app, people=people, base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    )
    if signed_in:
        person = User(id="id-of-a-person", username=USERNAME, role=Role.USER)
        session = people.open_session_for(person, account_language=account)
        hand_over_session_cookies(lobby.client, session, lobby.config)
    return lobby


def choose(lobby: Lobby, *, theme: ThemeChoice) -> None:
    lobby.client.put("/api/account", json={"theme": theme.value})


def test_a_signed_out_caller_gets_the_instances_language_and_theme_and_no_person() -> (
    None
):
    lobby = a_lobby()

    answer = lobby.client.get(_SESSION)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    view = SessionView.model_validate(answer.json())
    assert view.person is None
    assert view.theme == ThemeChoice.FOLLOW_SYSTEM
    assert view.language == DEFAULT_LANGUAGE
    assert view.sign_in.start == START_SIGN_IN_PATH
    assert view.sign_in.logout == LOGOUT_PATH
    assert view.sign_in.talk_start == f"{A_TALK_ADDRESS}{START_SIGN_IN_PATH}"


@pytest.mark.parametrize("signed_in", [False, True])
def test_the_session_names_the_central_account_at_the_configured_issuer(
    *, signed_in: bool
) -> None:
    lobby = a_lobby()
    if signed_in:
        lobby.sign_in()

    body = lobby.client.get(_SESSION).json()

    assert body["sign_in"] == {
        "start": START_SIGN_IN_PATH,
        "logout": LOGOUT_PATH,
        "talk_start": f"{A_TALK_ADDRESS}{START_SIGN_IN_PATH}",
        "account_console_url": f"{AN_ISSUER}/account",
    }
    assert set(body) == {"person", "theme", "language", "sign_in"}


def test_a_persons_own_theme_wins_and_the_instances_language_is_german() -> None:
    admin = a_lobby()
    admin.sign_in()
    admin.client.put(
        "/api/settings",
        json={"name": "Heim", "language_tag": "de"},
    )
    member = admin.another_browser()
    member.sign_in(username=NEIGHBOUR)
    choose(member, theme=ThemeChoice.DARK)

    body = member.client.get(_SESSION).json()

    assert body["person"] == {"username": NEIGHBOUR, "role": "user"}
    assert body["theme"] == "dark"
    assert body["language"] == "de"


def test_an_admin_is_told_apart_by_role() -> None:
    lobby = a_lobby()
    lobby.sign_in()

    assert lobby.client.get(_SESSION).json()["person"] == {
        "username": USERNAME,
        "role": "admin",
    }


@pytest.mark.parametrize(
    ("instance", "account", "spoken"),
    [
        ("en", "de", "de"),
        ("de", "en", "en"),
        ("en", "fr", "en"),
        ("de", "fr", "de"),
        ("de", None, "de"),
        ("fr", None, "en"),
        ("fr", "fr", "en"),
        ("fr", "de", "de"),
        ("en", "de-AT", "de"),
    ],
    ids=[
        "offered-beats-instance",
        "offered-english-beats-german-instance",
        "unoffered-answers-instance",
        "unoffered-answers-german-instance",
        "none-answers-instance",
        "gone-instance-catalog-none",
        "gone-instance-catalog-unoffered",
        "gone-instance-catalog-offered",
        "region-of-an-offered-language",
    ],
)
def test_the_language_follows_the_account_language_where_a_catalog_has_it(
    instance: str, account: str | None, spoken: str
) -> None:
    """LB1."""
    lobby = a_lobby_speaking(instance=instance, account=account)

    answer = lobby.client.get(_SESSION)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["language"] == spoken


@pytest.mark.parametrize("instance", ["en", "de"])
def test_a_signed_out_caller_reads_in_the_instances_language(instance: str) -> None:
    lobby = a_lobby_speaking(instance=instance, signed_in=False)

    answer = lobby.client.get(_SESSION)

    assert answer.json()["language"] == instance


def test_the_lobby_shells_html_lang_follows_the_account_language() -> None:
    lobby = a_lobby_speaking(instance="en", account="de")

    page = lobby.client.get("/an-address-the-app-decides").text

    assert 'lang="de"' in page


def test_a_browsers_accept_language_changes_nothing_for_an_account_without_one() -> (
    None
):
    lobby = a_lobby_speaking(instance="en", account=None)

    answer = lobby.client.get(_SESSION, headers={"accept-language": "de"})

    assert answer.json()["language"] == "en"


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


class _UnreadablePersonPreferences(FakePersonPreferencesStore):
    def read(self, user_id: str) -> PersonPreferences | None:
        message = f"preference store unavailable for {user_id}"
        raise RuntimeError(message)


def test_a_preference_store_that_fails_is_an_error_never_a_default() -> None:
    lobby = a_lobby(person_preferences=_UnreadablePersonPreferences())
    lobby.sign_in()
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
    owner.put("/api/account", json={"theme": "dark"})

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
