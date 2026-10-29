"""`/api/account` and `/api/settings` read and write the choices as data."""

from dataclasses import replace
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.contracts.text import Catalogs
from tests.presentator.application.support.fakes import FakePersonPreferencesStore
from tests.support.lobby import (
    ENGLISH,
    NEIGHBOUR,
    Lobby,
    a_lobby,
    a_lobby_that_claims_no_origin_of_its_own,
)
from tests.support.sign_in_support import ADMIN, MEMBER, Scene, a_scene

_ACCOUNT = "/api/account"
_SETTINGS = "/api/settings"
_SESSION = "/api/session"
_JSON = {"accept": "application/json"}
_HAL = "hal-client"
_GERMAN = replace(ENGLISH, language_tag="de", language_name="Deutsch")
_LANGUAGES = [
    {"tag": "de", "name": "Deutsch"},
    {"tag": "en", "name": ENGLISH.language_name},
]
_ANOTHER_SITE = "https://evil.example"


def a_german_lobby() -> Lobby:
    return a_lobby(
        catalogs=Catalogs(by_tag={ENGLISH.language_tag: ENGLISH, "de": _GERMAN})
    )


def an_admin_lobby() -> Lobby:
    lobby = a_german_lobby()
    lobby.sign_in()
    return lobby


def a_member_lobby() -> Lobby:
    lobby = a_german_lobby()
    lobby.sign_in(username=NEIGHBOUR)
    return lobby


def an_account(language: str | None, theme: str | None) -> dict[str, str | None]:
    return {"language_tag": language, "theme": theme}


def some_settings(
    language: str = "de", theme: str = "dark", name: str = "Heim"
) -> dict[str, str]:
    return {"name": name, "language_tag": language, "theme": theme}


def the_session(lobby: Lobby) -> dict[str, object]:
    return lobby.client.get(_SESSION).json()


def without_a_stored_choice(lobby: Lobby) -> None:
    assert lobby.client.get(_ACCOUNT).json()["theme"] is None
    assert lobby.client.get(_ACCOUNT).json()["language_tag"] is None


def is_a_problem(answer: Response, status: HTTPStatus) -> None:
    assert answer.status_code == status
    assert answer.headers["content-type"] == PROBLEM_MEDIA_TYPE
    assert answer.json()["status"] == status.value


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", _ACCOUNT), ("put", _ACCOUNT), ("get", _SETTINGS), ("put", _SETTINGS)],
)
def test_a_signed_out_caller_gets_webauths_refusal_not_a_problem(
    method: str, path: str
) -> None:
    lobby = a_german_lobby()

    answer = lobby.client.request(method, path, headers=_JSON)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE


def test_a_signed_out_browser_is_sent_to_sign_in() -> None:
    lobby = a_german_lobby()

    answer = lobby.client.get(_ACCOUNT, headers={"accept": "text/html"})

    assert answer.status_code == HTTPStatus.FOUND


def test_a_member_without_overrides_reads_nothing_chosen_and_the_language_list() -> (
    None
):
    lobby = a_member_lobby()

    answer = lobby.client.get(_ACCOUNT)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "language_tag": None,
        "theme": None,
        "languages": _LANGUAGES,
    }


def test_a_member_keeps_a_language_and_theme_that_the_session_then_serves() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(_ACCOUNT, json=an_account("de", "dark"))

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "language_tag": "de",
        "theme": "dark",
        "languages": _LANGUAGES,
    }
    assert lobby.client.get(_ACCOUNT).json() == answer.json()
    assert the_session(lobby)["theme"] == "dark"


def test_a_member_who_clears_both_overrides_gets_the_instance_defaults_again() -> None:
    lobby = a_member_lobby()
    lobby.client.put(_ACCOUNT, json=an_account("de", "dark"))

    answer = lobby.client.put(_ACCOUNT, json=an_account(None, None))

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["language_tag"] is None
    assert the_session(lobby)["theme"] == "follow-system"


def test_an_unspoken_language_on_account_is_a_problem_and_writes_nothing() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(_ACCOUNT, json=an_account("xx", "dark"))

    is_a_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY)
    without_a_stored_choice(lobby)


@pytest.mark.parametrize(
    "body",
    [an_account("en", "purple"), {"language_tag": "en"}, {"theme": "dark"}, {}],
    ids=["unknown theme", "theme missing", "language missing", "empty"],
)
def test_a_malformed_account_body_keeps_the_frameworks_answer_and_writes_nothing(
    body: dict[str, str | None],
) -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(_ACCOUNT, json=body)

    assert answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE
    without_a_stored_choice(lobby)


@pytest.mark.parametrize("method", ["get", "put"])
def test_a_member_is_turned_away_from_settings_with_a_problem(method: str) -> None:
    lobby = a_member_lobby()

    answer = lobby.client.request(
        method, _SETTINGS, json=some_settings() if method == "put" else None
    )

    is_a_problem(answer, HTTPStatus.FORBIDDEN)


def test_a_member_is_refused_before_the_body_is_even_read() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(_SETTINGS, json={"nonsense": True})

    is_a_problem(answer, HTTPStatus.FORBIDDEN)


def test_a_refused_member_wrote_nothing_to_the_instance() -> None:
    admin = an_admin_lobby()
    member = admin.another_browser()
    member.sign_in(username=NEIGHBOUR)

    member.client.put(_SETTINGS, json=some_settings())

    assert admin.client.get(_SETTINGS).json()["theme"] == "follow-system"


def test_an_admin_reads_the_defaults_of_a_fresh_instance() -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.get(_SETTINGS)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {
        "name": "",
        "language_tag": ENGLISH.language_tag,
        "theme": "follow-system",
        "languages": _LANGUAGES,
    }


def test_an_admins_instance_defaults_reach_a_member_without_overrides() -> None:
    admin = an_admin_lobby()
    member = admin.another_browser()
    member.sign_in(username=NEIGHBOUR)

    answer = admin.client.put(_SETTINGS, json=some_settings())

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {**some_settings(), "languages": _LANGUAGES}
    assert admin.client.get(_SETTINGS).json() == answer.json()
    assert member.client.get(_SESSION).json()["theme"] == "dark"


def test_an_unspoken_language_on_settings_is_a_problem_and_writes_nothing() -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.put(_SETTINGS, json=some_settings(language="xx"))

    is_a_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY)
    assert lobby.client.get(_SETTINGS).json()["name"] == ""


def test_an_admin_writing_account_changes_only_their_own_overrides() -> None:
    lobby = an_admin_lobby()

    lobby.client.put(_ACCOUNT, json=an_account("de", "light"))

    settings = lobby.client.get(_SETTINGS).json()
    assert settings["theme"] == "follow-system"
    assert settings["language_tag"] == ENGLISH.language_tag
    assert lobby.client.get(_ACCOUNT).json()["theme"] == "light"


@pytest.mark.parametrize("method", ["post", "delete"])
@pytest.mark.parametrize("path", [_ACCOUNT, _SETTINGS])
def test_other_methods_get_the_frameworks_405(method: str, path: str) -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.request(method, path)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED


def test_a_failing_store_is_the_plain_500_not_a_problem() -> None:
    class Unreadable(FakePersonPreferencesStore):
        def read(self, user_id: str) -> None:
            message = f"unavailable for {user_id}"
            raise RuntimeError(message)

    lobby = a_lobby(person_preferences=Unreadable())
    lobby.sign_in(username=NEIGHBOUR)
    quiet = TestClient(
        lobby.app,
        base_url=str(lobby.client.base_url),
        follow_redirects=False,
        headers=dict(lobby.client.headers),
        raise_server_exceptions=False,
    )
    quiet.cookies.update(lobby.client.cookies)

    answer = quiet.get(_ACCOUNT)

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE


def a_signed_in_browser_that_claims_nothing() -> Lobby:
    lobby = a_lobby_that_claims_no_origin_of_its_own()
    lobby.sign_in()
    return lobby


def a_write(lobby: Lobby, headers: dict[str, str]) -> Response:
    return lobby.client.put(
        _SETTINGS, json=some_settings(language="en"), headers={**_JSON, **headers}
    )


def settings_stayed_fresh(lobby: Lobby) -> None:
    assert (
        lobby.client.get(_SETTINGS, headers={"sec-fetch-site": "same-origin"}).json()[
            "theme"
        ]
        == "follow-system"
    )


def test_a_cross_site_write_is_refused_even_with_a_matching_origin() -> None:
    lobby = a_signed_in_browser_that_claims_nothing()

    answer = a_write(
        lobby, {"sec-fetch-site": "cross-site", "origin": lobby.own_origin()}
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    settings_stayed_fresh(lobby)


def test_a_same_site_write_with_the_lobbys_own_origin_passes() -> None:
    lobby = a_signed_in_browser_that_claims_nothing()

    answer = a_write(
        lobby, {"sec-fetch-site": "same-site", "origin": lobby.own_origin()}
    )

    assert answer.status_code == HTTPStatus.OK


def test_a_same_site_write_with_a_foreign_origin_is_refused() -> None:
    lobby = a_signed_in_browser_that_claims_nothing()

    answer = a_write(lobby, {"sec-fetch-site": "same-site", "origin": _ANOTHER_SITE})

    assert answer.status_code == HTTPStatus.FORBIDDEN
    settings_stayed_fresh(lobby)


def test_a_write_with_neither_header_is_refused() -> None:
    lobby = a_signed_in_browser_that_claims_nothing()

    answer = a_write(lobby, {})

    assert answer.status_code == HTTPStatus.FORBIDDEN
    settings_stayed_fresh(lobby)


def test_a_cross_site_account_write_is_refused_too() -> None:
    lobby = a_signed_in_browser_that_claims_nothing()

    answer = lobby.client.put(
        _ACCOUNT,
        json=an_account("de", "dark"),
        headers={
            **_JSON,
            "sec-fetch-site": "cross-site",
            "origin": lobby.own_origin(),
        },
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    lobby.client.headers["sec-fetch-site"] = "same-origin"
    assert lobby.client.get(_ACCOUNT).json()["theme"] is None


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({_HAL}))


def a_bearer(scene: Scene, *, subject: str, groups: list[str]) -> dict[str, str]:
    minted = scene.issuer.mint_access_token(
        subject=subject, groups=groups, authorized_party=_HAL, audience=None
    )
    return {"authorization": f"Bearer {minted}", **_JSON}


def a_program(scene: Scene) -> TestClient:
    """A non-browser client: it claims no origin at all."""
    return TestClient(scene.lobby.app, base_url=str(scene.lobby.client.base_url))


def test_a_members_bearer_reads_and_writes_the_members_own_account_without_origin(
    scene: Scene,
) -> None:
    program = a_program(scene)
    bearer = a_bearer(scene, subject="member-subject", groups=MEMBER)

    written = program.put(_ACCOUNT, json=an_account("en", "dark"), headers=bearer)
    read = program.get(_ACCOUNT, headers=bearer)

    assert written.status_code == HTTPStatus.OK
    assert read.json()["theme"] == "dark"
    assert read.json()["language_tag"] == "en"


@pytest.mark.parametrize("method", ["get", "put"])
def test_a_members_bearer_is_turned_away_from_settings(
    scene: Scene, method: str
) -> None:
    program = a_program(scene)
    bearer = a_bearer(scene, subject="member-subject", groups=MEMBER)

    answer = program.request(
        method,
        _SETTINGS,
        json=some_settings() if method == "put" else None,
        headers=bearer,
    )

    is_a_problem(answer, HTTPStatus.FORBIDDEN)


def test_an_admins_bearer_writes_the_instance_defaults(scene: Scene) -> None:
    program = a_program(scene)
    bearer = a_bearer(scene, subject="admin-subject", groups=ADMIN)

    answer = program.put(_SETTINGS, json=some_settings(language="en"), headers=bearer)

    assert answer.status_code == HTTPStatus.OK
    assert program.get(_SETTINGS, headers=bearer).json()["theme"] == "dark"


def test_an_invalid_bearer_beside_an_admin_cookie_writes_nothing(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN)

    answer = browser.put(
        _SETTINGS,
        json=some_settings(language="en"),
        headers={"authorization": "Bearer nonsense", **_JSON},
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert browser.get(_SETTINGS, headers=_JSON).json()["theme"] == "follow-system"
