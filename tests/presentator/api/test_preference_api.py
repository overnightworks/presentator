"""`/api/account` and `/api/settings` read and write the choices as data."""

from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.openapi.utils import (
    validation_error_definition,
    validation_error_response_definition,
)
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.contracts.preferences import InstanceSettings, ThemeChoice
from tests.presentator.application.support.fakes import FakePersonPreferencesStore
from tests.support.lobby import (
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
_ANOTHER_SITE = "https://evil.example"


def an_admin_lobby() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in()
    return lobby


def a_member_lobby() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in(username=NEIGHBOUR)
    return lobby


def an_account(theme: str | None) -> dict[str, str | None]:
    return {"theme": theme}


def some_settings(name: str = "Heim") -> dict[str, str]:
    return {"name": name}


def the_session(lobby: Lobby) -> dict[str, object]:
    return lobby.client.get(_SESSION).json()


def without_a_stored_choice(lobby: Lobby) -> None:
    assert lobby.client.get(_ACCOUNT).json()["theme"] == (
        ThemeChoice.FOLLOW_SYSTEM.value
    )


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
    lobby = a_lobby()

    answer = lobby.client.request(method, path, headers=_JSON)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE


def test_a_signed_out_browser_is_sent_to_sign_in() -> None:
    lobby = a_lobby()

    answer = lobby.client.get(_ACCOUNT, headers={"accept": "text/html"})

    assert answer.status_code == HTTPStatus.FOUND


def test_a_member_without_a_choice_reads_follow_system() -> None:
    """LB2."""
    lobby = a_member_lobby()

    answer = lobby.client.get(_ACCOUNT)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {"theme": ThemeChoice.FOLLOW_SYSTEM.value}


def test_a_member_keeps_a_theme_that_the_session_then_serves() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(_ACCOUNT, json=an_account(ThemeChoice.DARK.value))

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {"theme": ThemeChoice.DARK.value}
    assert lobby.client.get(_ACCOUNT).json() == answer.json()
    assert the_session(lobby)["theme"] == ThemeChoice.DARK.value


def test_a_theme_chosen_in_the_menu_leaves_the_neighbours_pages_alone() -> None:
    admin = an_admin_lobby()
    admin.client.put(_ACCOUNT, json=an_account(ThemeChoice.DARK.value))
    neighbour = admin.another_browser()
    neighbour.sign_in(username=NEIGHBOUR)

    assert the_session(neighbour)["theme"] == ThemeChoice.FOLLOW_SYSTEM.value


def test_following_the_system_again_stores_follow_system() -> None:
    lobby = a_member_lobby()
    lobby.client.put(_ACCOUNT, json=an_account(ThemeChoice.DARK.value))

    lobby.client.put(_ACCOUNT, json=an_account(ThemeChoice.FOLLOW_SYSTEM.value))

    assert the_session(lobby)["theme"] == ThemeChoice.FOLLOW_SYSTEM.value
    assert lobby.client.get(_ACCOUNT).json() == an_account(
        ThemeChoice.FOLLOW_SYSTEM.value
    )


@pytest.mark.parametrize(
    "body",
    [
        an_account("purple"),
        an_account(None),
        {"name": "Heim"},
        {},
    ],
    ids=["unknown theme", "no theme", "theme missing", "empty"],
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
    """LB4."""
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

    assert admin.client.get(_SETTINGS).json()["name"] == InstanceSettings().name


def test_an_admin_reads_the_defaults_of_a_fresh_instance() -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.get(_SETTINGS)

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["cache-control"] == "no-store"
    assert answer.json() == {"name": InstanceSettings().name}


def test_an_admin_saves_the_instance_name_and_a_member_keeps_follow_system() -> None:
    admin = an_admin_lobby()
    member = admin.another_browser()
    member.sign_in(username=NEIGHBOUR)

    answer = admin.client.put(_SETTINGS, json=some_settings())

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == some_settings()
    assert admin.client.get(_SETTINGS).json() == answer.json()
    assert member.client.get(_SESSION).json()["theme"] == (
        ThemeChoice.FOLLOW_SYSTEM.value
    )


def test_an_admin_saves_an_empty_instance_name_and_reads_it_back_empty() -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.put(_SETTINGS, json=some_settings(name=""))

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["name"] == ""
    assert lobby.client.get(_SETTINGS).json()["name"] == ""


def test_an_admin_writing_account_changes_only_their_own_overrides() -> None:
    lobby = an_admin_lobby()

    lobby.client.put(_ACCOUNT, json=an_account(ThemeChoice.LIGHT.value))

    settings = lobby.client.get(_SETTINGS).json()
    assert settings["name"] == InstanceSettings().name
    assert lobby.client.get(_ACCOUNT).json()["theme"] == ThemeChoice.LIGHT.value


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
        _SETTINGS, json=some_settings(), headers={**_JSON, **headers}
    )


def settings_stayed_fresh(lobby: Lobby) -> None:
    assert (
        lobby.client.get(_SETTINGS, headers={"sec-fetch-site": "same-origin"}).json()[
            "name"
        ]
        == InstanceSettings().name
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
        json=an_account(ThemeChoice.DARK.value),
        headers={
            **_JSON,
            "sec-fetch-site": "cross-site",
            "origin": lobby.own_origin(),
        },
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    lobby.client.headers["sec-fetch-site"] = "same-origin"
    assert lobby.client.get(_ACCOUNT).json()["theme"] == (
        ThemeChoice.FOLLOW_SYSTEM.value
    )


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

    written = program.put(
        _ACCOUNT, json=an_account(ThemeChoice.DARK.value), headers=bearer
    )
    read = program.get(_ACCOUNT, headers=bearer)

    assert written.status_code == HTTPStatus.OK
    assert read.json()["theme"] == ThemeChoice.DARK.value


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

    answer = program.put(_SETTINGS, json=some_settings(), headers=bearer)

    assert answer.status_code == HTTPStatus.OK
    assert (
        program.get(_SETTINGS, headers=bearer).json()["name"]
        == (some_settings()["name"])
    )


def test_an_invalid_bearer_beside_an_admin_cookie_writes_nothing(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN)

    answer = browser.put(
        _SETTINGS,
        json=some_settings(),
        headers={"authorization": "Bearer nonsense", **_JSON},
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert (
        browser.get(_SETTINGS, headers=_JSON).json()["name"] == InstanceSettings().name
    )


@pytest.mark.parametrize("path", [_ACCOUNT, _SETTINGS])
def test_the_generated_spec_resolves_the_put_422_to_the_frameworks_validation_schema(
    path: str,
) -> None:
    spec = an_admin_lobby().app.openapi()
    answer = spec["paths"][path]["put"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"]
    components = spec["components"]["schemas"]

    resolved = components[answer["$ref"].rsplit("/", 1)[-1]]

    assert resolved == validation_error_response_definition
    item = resolved["properties"]["detail"]["items"]["$ref"].rsplit("/", 1)[-1]
    assert components[item] == validation_error_definition
