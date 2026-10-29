"""A person's hal access token on `/api/` is that person, and makes no session.

The lobby runs over the real SQLite identity stores and the fake issuer; `/api/nothing`
has no route, so a 404 shows the gate admitted the request.
"""

import sqlite3
from contextlib import closing
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from webauth.ports import AccountEvent, AccountEventKind

from tests.support.lobby import A_BROWSERS_ADDRESS
from tests.support.sign_in_support import ADMIN, MEMBER, Scene, a_scene

HAL = "hal-client"
THE_SUBJECT = "keycloak-subject-of-a-member"
_UNSERVED = "/api/nothing"
_OTHER_CLIENT = "another-client"


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene(tmp_path, token_clients=frozenset({HAL}))


def a_token_client(
    scene: Scene, *, headers: dict[str, str] | None = None
) -> TestClient:
    """A program that is no browser: it sends only what it names."""
    return TestClient(
        scene.lobby.app,
        base_url=A_BROWSERS_ADDRESS,
        follow_redirects=False,
        headers=headers or {},
    )


def bearer(
    scene: Scene,
    *,
    subject: str = THE_SUBJECT,
    groups: list[str] | None = None,
    authorized_party: str = HAL,
    audience: str | None = None,
) -> dict[str, str]:
    minted = scene.issuer.mint_access_token(
        subject=subject,
        groups=MEMBER if groups is None else groups,
        authorized_party=authorized_party,
        audience=audience,
    )
    return {"authorization": f"Bearer {minted}", "accept": "application/json"}


def stored_expiries(scene: Scene) -> list[str]:
    with closing(sqlite3.connect(scene.database)) as connection:
        return [row[0] for row in connection.execute("SELECT expires_at FROM sessions")]


def kinds(scene: Scene) -> list[AccountEventKind]:
    return [event.kind for event in scene.audit.events]


def test_a_members_token_is_admitted_as_that_person_without_a_session(
    scene: Scene,
) -> None:
    """P1: the first call makes the account, a second makes nothing new."""
    program = a_token_client(scene)

    first = program.get(_UNSERVED, headers=bearer(scene))
    second = program.get(_UNSERVED, headers=bearer(scene))

    assert (first.status_code, second.status_code) == (
        HTTPStatus.NOT_FOUND,
        HTTPStatus.NOT_FOUND,
    )
    assert (scene.count("users"), scene.count("sessions")) == (1, 0)
    assert kinds(scene) == [AccountEventKind.USER_CREATED]


def test_an_expired_token_is_refused_and_writes_nothing(scene: Scene) -> None:
    """P2: past `exp` plus leeway the token is invalid, and no account is made."""
    headers = bearer(scene)
    scene.clock.advance(timedelta(hours=1))

    answer = a_token_client(scene).get(_UNSERVED, headers=headers)

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert (scene.count("users"), kinds(scene)) == (0, [])


@pytest.mark.parametrize(
    ("audience", "authorized_party"),
    [("somebody-elses-client", HAL), (None, _OTHER_CLIENT)],
    ids=["another audience", "a client no one named"],
)
def test_a_token_for_another_audience_or_client_is_refused(
    scene: Scene, audience: str | None, authorized_party: str
) -> None:
    """P3: audience and `azp` both decide, and nothing is written."""
    answer = a_token_client(scene).get(
        _UNSERVED,
        headers=bearer(scene, audience=audience, authorized_party=authorized_party),
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert (scene.count("users"), kinds(scene)) == (0, [])


def test_a_subject_outside_the_app_has_no_access_and_no_account(scene: Scene) -> None:
    """P4: another app's person is refused, and no account is made."""
    answer = a_token_client(scene).get(
        _UNSERVED, headers=bearer(scene, groups=["/apps/another-app"])
    )

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert 'error="insufficient_scope"' in answer.headers["www-authenticate"]
    assert scene.count("users") == 0


def test_without_a_credential_a_program_gets_a_challenge_and_a_browser_the_login(
    scene: Scene,
) -> None:
    """P5: a program is told to bring a bearer; a browser is sent to sign in."""
    program = a_token_client(scene).get(
        _UNSERVED, headers={"accept": "application/json"}
    )
    browser = scene.browser().get(_UNSERVED)

    assert program.status_code == HTTPStatus.UNAUTHORIZED
    assert program.headers["www-authenticate"].startswith('Bearer realm="api"')
    assert browser.status_code == HTTPStatus.FOUND
    assert browser.headers["location"].startswith("/login/oidc")


def test_a_cookie_alone_is_still_a_signed_in_person_on_api(scene: Scene) -> None:
    """P6: the cookie decides when no token is sent."""
    browser = scene.browser()
    scene.sign_in(browser)

    answer = browser.get(_UNSERVED)

    assert answer.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("groups", "refused_as_insufficient_scope"),
    [(MEMBER, True), (ADMIN, False)],
    ids=["a member", "an admin"],
)
def test_the_admin_prefix_stays_a_machine_path_and_provisions_no_caller(
    scene: Scene, groups: list[str], refused_as_insufficient_scope: bool
) -> None:
    """P7: a path both rules select is a machine path; the caller gets no account."""
    answer = a_token_client(scene).delete(
        "/api/admin/people/someone", headers=bearer(scene, groups=groups)
    )

    challenge = answer.headers.get("www-authenticate", "")
    assert (
        'realm="admin", error="insufficient_scope"' in challenge
    ) is refused_as_insufficient_scope
    assert (answer.status_code == HTTPStatus.FORBIDDEN) is refused_as_insufficient_scope
    assert answer.status_code != HTTPStatus.UNAUTHORIZED
    assert scene.count("users") == 0


def test_the_webhook_secret_as_a_bearer_is_no_token_when_clients_are_named(
    scene: Scene,
) -> None:
    """P12: a path neither rule selects is left to the hook, whatever bearer it carries."""
    answer = a_token_client(scene).post(
        "/sources/alpha/fetch", headers={"authorization": "Bearer a-webhook-secret"}
    )

    assert 'error="invalid_token"' not in answer.headers.get("www-authenticate", "")
    assert scene.count("users") == 0


def test_a_token_is_its_own_person_beside_another_persons_cookie(scene: Scene) -> None:
    """P8a: the token's account is admitted; the cookie's session is untouched."""
    browser = scene.browser()
    scene.sign_in(browser, subject="the-cookies-person")
    expiries = stored_expiries(scene)
    users_before = scene.count("users")
    scene.audit.events.clear()

    answer = browser.get(_UNSERVED, headers=bearer(scene))

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert scene.count("users") == users_before + 1
    assert kinds(scene) == [AccountEventKind.USER_CREATED]
    assert (scene.count("sessions"), stored_expiries(scene)) == (1, expiries)


@pytest.mark.parametrize(
    ("first", "then", "role_change"),
    [(MEMBER, ADMIN, ("user", "admin")), (ADMIN, MEMBER, ("admin", "user"))],
    ids=["promotion", "demotion"],
)
def test_the_role_follows_the_token_and_no_session_is_made(
    scene: Scene,
    first: list[str],
    then: list[str],
    role_change: tuple[str, str],
) -> None:
    """P8b: a known account's role is the token's groups, recorded as a change."""
    program = a_token_client(scene)
    program.get(_UNSERVED, headers=bearer(scene, groups=first))

    program.get(_UNSERVED, headers=bearer(scene, groups=then))

    created, changed = scene.audit.events
    assert isinstance(changed, AccountEvent)
    assert (changed.kind, changed.previous_role, changed.role) == (
        AccountEventKind.ROLE_CHANGED,
        *role_change,
    )
    assert changed.subject_id == created.subject_id
    assert (scene.count("users"), scene.count("sessions")) == (1, 0)


def test_an_invalid_token_beside_a_valid_cookie_does_not_fall_back_to_the_cookie(
    scene: Scene,
) -> None:
    """P8: the bad bearer is refused and the session is not renewed."""
    browser = scene.browser()
    scene.sign_in(browser)
    expiries = stored_expiries(scene)
    scene.clock.advance(timedelta(minutes=1))

    answer = browser.get(_UNSERVED, headers={"authorization": "Bearer not-a-token"})

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert stored_expiries(scene) == expiries


def test_a_token_post_needs_no_provenance_header_but_a_cookie_post_does(
    scene: Scene,
) -> None:
    """P9: a program sends no Origin; a browser cookie without one is refused."""
    by_token = a_token_client(scene).post(_UNSERVED, headers=bearer(scene))
    signed_in = scene.browser()
    scene.sign_in(signed_in)
    cookie_only = a_token_client(scene)
    cookie_only.cookies.update(signed_in.cookies)
    by_cookie = cookie_only.post(_UNSERVED)

    assert by_token.status_code in {HTTPStatus.NOT_FOUND, HTTPStatus.METHOD_NOT_ALLOWED}
    assert by_cookie.status_code == HTTPStatus.FORBIDDEN
    assert by_cookie.json() == {"detail": "Missing Origin header"}


def test_a_page_is_not_reachable_by_token(scene: Scene) -> None:
    """P10: only `/api/` takes the bearer; a page sends the browser to sign in."""
    answer = scene.browser().get("/", headers=bearer(scene) | {"accept": "text/html"})

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"].startswith("/login")
    assert scene.count("users") == 0


def test_without_a_named_client_a_token_is_a_signed_out_call(tmp_path: Path) -> None:
    """P13: no gate stands, so the answer is a signed-out 401 with no challenge."""
    bare = a_scene(tmp_path)

    answer = a_token_client(bare).get(_UNSERVED, headers=bearer(bare))

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert "www-authenticate" not in answer.headers
    assert bare.count("users") == 0
