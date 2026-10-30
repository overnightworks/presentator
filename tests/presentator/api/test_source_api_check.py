"""An admin drafts a deploy key and checks a source address through the JSON API."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.application.decks import (
    DRAFT_MAX_AGE,
    RESERVED_SOURCE_NAME,
    Decks,
    SourceRefusal,
)
from presentator.contracts.decks import ConnectionCheckResult, SourceRunFailure
from presentator.contracts.models import Role
from tests.presentator.api.support.source_api_support import (
    ASKING_FOR_JSON,
    SAME_ORIGIN,
    Instance,
    a_bearer,
    a_client_with_no_header_of_its_own,
    a_lobby_over,
    a_member_over,
    a_scene_that_must_not_run,
    an_admin_bearer_instance,
    an_admin_cookie_instance,
    an_admin_over,
    assert_no_store,
    assert_nothing_returned,
    assert_problem,
    assert_validation_answer,
    decks_over,
    decks_that_must_not_run,
    without_raising,
)
from tests.presentator.application.support.fakes import (
    A_REACHABLE_CHECK,
    FakeConnectionChecker,
    FakeDeployKeyDrafts,
    FakeSourceStore,
    FrozenClock,
)
from tests.support.lobby import ADMIN, Lobby
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, Scene

_NEW = f"/api/sources/{RESERVED_SOURCE_NAME}"
_KEY_DRAFT = f"{_NEW}/key-draft"
_CHECK = f"{_NEW}/check"
_SENT = "the-token-this-scenario-sends"
_HTTPS = "https://git.example.invalid/talks.git"
_SSH = "git@git.example.invalid:talks.git"
_USER_PART = "https://user:pw@git.example.invalid/talks.git"
_REFUSED_TYPE = "tag:hallucinai.de,2026-09-08:presentator/problems/source-refused"
_GIT_SAID = "git said something of its own"


@dataclass
class _RecordingChecker(FakeConnectionChecker):
    asked: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def check(self, *, url: str, ref: str, secret: str) -> ConnectionCheckResult:
        self.asked.append((url, secret))
        return self.answer


class _CheckerThatBreaks(_RecordingChecker):
    def check(self, *, url: str, ref: str, secret: str) -> ConnectionCheckResult:
        raise RuntimeError(secret)


@dataclass(frozen=True, slots=True)
class _World:
    """The deck use cases an admin's requests reach, and what they can be read from."""

    decks: Decks
    clock: FrozenClock
    checker: _RecordingChecker
    store: FakeSourceStore
    drafts: FakeDeployKeyDrafts


def a_world(checker: _RecordingChecker | None = None) -> _World:
    store = FakeSourceStore()
    drafts = FakeDeployKeyDrafts()
    clock = FrozenClock(instant=decks_over(store).clock.now())
    recording = _RecordingChecker() if checker is None else checker
    decks = replace(
        decks_over(store), checker=recording, clock=clock, key_drafts=drafts
    )
    return _World(decks, clock, recording, store, drafts)


def _cookie(world: _World, _tmp_path: Path) -> Instance:
    return an_admin_cookie_instance(world.decks, world.store)


def _bearer(world: _World, tmp_path: Path) -> Instance:
    return an_admin_bearer_instance(world.decks, world.store, tmp_path)


@pytest.fixture(params=[_cookie, _bearer], ids=["cookie", "bearer"])
def an_admin(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Callable[[_World], Instance]:
    return lambda world: request.param(world, tmp_path)


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene_that_must_not_run(tmp_path)


def _checked_body(**changes: object) -> dict[str, object]:
    return {
        "url": _HTTPS,
        "access": "https-token",
        "secret": _SENT,
        "key_draft_id": None,
    } | changes


def _draft(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    return client.post(_KEY_DRAFT, headers=headers)


def _check(client: TestClient, headers: dict[str, str] | None = None) -> Response:
    return client.post(_CHECK, json=_checked_body(), headers=headers)


_ROUTES = pytest.mark.parametrize("send", [_draft, _check], ids=["key-draft", "check"])
_NOT_SENT_BY_THE_SAME_ORIGIN = pytest.mark.parametrize(
    "headers",
    [
        pytest.param(
            {"sec-fetch-site": "cross-site", "origin": "https://testserver"},
            id="cross-site",
        ),
        pytest.param(
            {"sec-fetch-site": "same-site", "origin": "https://elsewhere.invalid"},
            id="same-site-with-a-foreign-origin",
        ),
        pytest.param({}, id="no-claim-at-all"),
    ],
)


# 1
@_ROUTES
def test_a_signed_out_caller_meets_webauths_answer_not_a_problem(
    send: Callable[..., Response],
) -> None:
    lobby = a_lobby_over(decks_that_must_not_run())

    asking_json = send(lobby.client, ASKING_FOR_JSON)
    asking_html = send(lobby.client, None)

    assert asking_json.status_code == HTTPStatus.UNAUTHORIZED
    assert asking_html.status_code == HTTPStatus.FOUND
    for answer in (asking_json, asking_html):
        assert answer.headers.get("content-type") != PROBLEM_MEDIA_TYPE
        assert_no_store(answer)
        assert_nothing_returned(answer, _SENT)


# 2
@_ROUTES
def test_a_member_is_refused_with_a_problem(send: Callable[..., Response]) -> None:
    lobby = a_member_over(decks_that_must_not_run())

    assert_problem(send(lobby.client), HTTPStatus.FORBIDDEN, _SENT)


# 3
@_ROUTES
def test_a_members_bearer_is_refused_with_a_problem(
    scene: Scene, send: Callable[..., Response]
) -> None:
    answer = send(a_client_with_no_header_of_its_own(scene), a_bearer(scene, MEMBER))

    assert_problem(answer, HTTPStatus.FORBIDDEN, _SENT)


# 4
@_ROUTES
def test_a_members_bearer_beside_an_admin_cookie_carries_the_members_rights(
    scene: Scene, send: Callable[..., Response]
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = send(browser, {**a_bearer(scene, MEMBER), **SAME_ORIGIN})

    assert_problem(answer, HTTPStatus.FORBIDDEN, _SENT)


# 5
@_ROUTES
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(
    scene: Scene, send: Callable[..., Response]
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = send(browser, {"authorization": "Bearer nonsense", **SAME_ORIGIN})

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert_no_store(answer)
    assert_nothing_returned(answer, _SENT)


# 6, 7
@_ROUTES
@_NOT_SENT_BY_THE_SAME_ORIGIN
def test_the_origin_check_refuses_these_writes_before_any_port_is_touched(
    send: Callable[..., Response], headers: dict[str, str]
) -> None:
    lobby = an_admin_over(decks_that_must_not_run(), headers={"accept": "text/html"})

    answer = send(lobby.client, headers)

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert_no_store(answer)
    assert_nothing_returned(answer, _SENT)


# 8
@_ROUTES
def test_a_same_site_write_with_the_own_origin_is_taken(
    send: Callable[..., Response],
) -> None:
    lobby = an_admin_over(a_world().decks, headers={"accept": "text/html"})

    answer = send(
        lobby.client,
        {"sec-fetch-site": "same-site", "origin": "https://testserver"},
    )

    assert answer.status_code == HTTPStatus.OK
    assert_no_store(answer)


# 9, 10
def test_an_admin_drafts_a_key_and_the_private_half_is_in_no_answer(
    an_admin: Callable[[_World], Instance],
) -> None:
    world = a_world()
    instance = an_admin(world)

    answer = _draft(instance.client, instance.headers)

    (stored,) = world.drafts.drafts.values()
    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {"id": stored.id, "public_key": stored.public_key}
    assert stored.owner_id == instance.person_id
    assert_no_store(answer)
    assert_nothing_returned(answer, stored.private_key)


# 11
def test_a_draft_is_reused_until_it_is_older_than_the_limit() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    first = _draft(client)
    again = _draft(client)
    world.clock.advance(DRAFT_MAX_AGE + timedelta(seconds=1))
    later = _draft(client)

    assert again.json() == first.json()
    assert later.json()["id"] != first.json()["id"]
    for answer in (first, again, later):
        assert_no_store(answer)


# 12
def test_two_admins_each_get_their_own_draft() -> None:
    world = a_world()
    first = an_admin_over(world.decks)
    second = first.another_browser()
    second.sign_in(username="second-admin", role=Role.ADMIN)

    mine = _draft(first.client)
    theirs = _draft(second.client)

    assert mine.json()["id"] != theirs.json()["id"]
    assert_no_store(mine)
    assert_no_store(theirs)
    assert set(world.drafts.drafts) == {ADMIN, "id-of-second-admin"}


# 13
def test_a_reachable_https_check_hands_the_token_to_the_checker_and_back_to_nobody(
    an_admin: Callable[[_World], Instance],
) -> None:
    world = a_world()
    instance = an_admin(world)

    answer = _check(instance.client, instance.headers)

    body = answer.json()
    assert answer.status_code == HTTPStatus.OK
    assert body["failure"] is None
    assert body["commit"] == A_REACHABLE_CHECK.commit
    assert body["fingerprint"]
    assert world.checker.asked == [(_HTTPS, _SENT)]
    assert_no_store(answer)
    assert_nothing_returned(answer, _SENT)


# 14
@pytest.mark.parametrize("failure", list(SourceRunFailure))
def test_an_unreachable_check_answers_its_failure_and_no_fingerprint(
    failure: SourceRunFailure,
) -> None:
    found = ConnectionCheckResult(failure=failure, commit=None, detail=_GIT_SAID)
    world = a_world(_RecordingChecker(answer=found))

    answer = _check(an_admin_over(world.decks).client)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {
        "failure": failure,
        "commit": None,
        "detail": _GIT_SAID if failure is SourceRunFailure.FAILED else None,
        "fingerprint": None,
    }
    assert_no_store(answer)
    assert_nothing_returned(answer, _SENT)


# 15
def test_an_ssh_check_probes_with_the_admins_own_draft_and_never_shows_it() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    drafted = _draft(client).json()
    (stored,) = world.drafts.drafts.values()

    answer = client.post(
        _CHECK,
        json=_checked_body(
            url=_SSH, access="ssh-deploy-key", secret=None, key_draft_id=drafted["id"]
        ),
    )

    assert answer.json()["failure"] is None
    assert world.checker.asked == [(_SSH, stored.private_key)]
    assert_no_store(answer)
    assert_nothing_returned(answer, stored.private_key)


@pytest.mark.parametrize("which", ["foreign", "stale"])
def test_an_ssh_check_with_a_foreign_or_stale_draft_is_refused_unprobed(
    which: str,
) -> None:
    world = a_world()
    admin = an_admin_over(world.decks)
    client = admin.client
    drafted = _draft(client).json()
    other = admin.another_browser()
    other.sign_in(username="second-admin", role=Role.ADMIN)
    sent = _draft(other.client).json()["id"] if which == "foreign" else drafted["id"]
    if which == "stale":
        world.clock.advance(DRAFT_MAX_AGE + timedelta(seconds=1))

    answer = client.post(
        _CHECK,
        json=_checked_body(
            url=_SSH, access="ssh-deploy-key", secret=None, key_draft_id=sent
        ),
    )

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["failure"] == SourceRunFailure.REFUSED
    assert answer.json()["fingerprint"] is None
    assert world.checker.asked == []
    assert_no_store(answer)


# 16
@pytest.mark.parametrize(
    "body",
    [
        _checked_body(access="ssh-deploy-key", secret=None),
        _checked_body(url=_USER_PART),
        _checked_body(access="local-folder", secret=None),
    ],
    ids=["ssh-for-https", "user-part", "folder-for-https"],
)
def test_an_access_that_does_not_match_the_url_is_a_typed_source_refused_problem(
    body: dict[str, object],
) -> None:
    world = a_world()

    answer = an_admin_over(world.decks).client.post(_CHECK, json=body)

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, _SENT, "pw")
    problem = answer.json()
    assert problem["reason"] == SourceRefusal.ACCESS_MISMATCH
    assert problem["title"] == HTTPStatus.UNPROCESSABLE_ENTITY.phrase
    assert problem["status"] == answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert problem["type"] == _REFUSED_TYPE
    assert world.checker.asked == []


# 17
@pytest.mark.parametrize(
    "body",
    [
        _checked_body(access="https"),
        {"access": "https-token", "secret": _SENT},
        _checked_body(secret=12345),
    ],
    ids=["radio-token", "url-missing", "secret-a-number"],
)
def test_a_malformed_check_is_the_validation_answer_without_what_was_sent(
    body: dict[str, object],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run())

    answer = lobby.client.post(_CHECK, json=body)

    assert_validation_answer(answer, _SENT, "12345")


# 18
@pytest.mark.parametrize("path", [_KEY_DRAFT, _CHECK])
def test_a_method_the_routes_do_not_offer_is_the_frameworks_405(path: str) -> None:
    lobby = an_admin_over(decks_that_must_not_run())

    answer = lobby.client.get(path, headers=SAME_ORIGIN)

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert_no_store(answer)


def test_the_reserved_name_is_no_source_page() -> None:
    lobby = an_admin_over(a_world().decks)

    answer = lobby.client.get(_NEW, headers=ASKING_FOR_JSON)

    assert_problem(answer, HTTPStatus.NOT_FOUND)


# 19
_SECRET_NUMBER = 7391825


@dataclass(frozen=True, slots=True)
class _Logged:
    send: Callable[[_World, Lobby], Response]
    status: HTTPStatus
    found: ConnectionCheckResult = A_REACHABLE_CHECK


def _ssh_body(draft_id: str) -> dict[str, object]:
    return _checked_body(
        url=_SSH, access="ssh-deploy-key", secret=None, key_draft_id=draft_id
    )


def _own_draft_check(_world: _World, lobby: Lobby) -> Response:
    drafted = _draft(lobby.client).json()
    return lobby.client.post(_CHECK, json=_ssh_body(drafted["id"]))


def _foreign_draft_check(_world: _World, lobby: Lobby) -> Response:
    other = lobby.another_browser()
    other.sign_in(username="second-admin", role=Role.ADMIN)
    theirs = _draft(other.client).json()
    return lobby.client.post(_CHECK, json=_ssh_body(theirs["id"]))


def _stale_draft_check(world: _World, lobby: Lobby) -> Response:
    drafted = _draft(lobby.client).json()
    world.clock.advance(DRAFT_MAX_AGE + timedelta(seconds=1))
    return lobby.client.post(_CHECK, json=_ssh_body(drafted["id"]))


def _expired_draft(world: _World, lobby: Lobby) -> Response:
    _draft(lobby.client)
    world.clock.advance(DRAFT_MAX_AGE + timedelta(seconds=1))
    return _draft(lobby.client)


def _second_admins_draft(_world: _World, lobby: Lobby) -> Response:
    other = lobby.another_browser()
    other.sign_in(username="second-admin", role=Role.ADMIN)
    _draft(lobby.client)
    return _draft(other.client)


def _posting(**changes: object) -> Callable[[_World, Lobby], Response]:
    return lambda _world, lobby: lobby.client.post(
        _CHECK, json=_checked_body(**changes)
    )


_LOGGED = {
    "10 draft": _Logged(lambda _w, lobby: _draft(lobby.client), HTTPStatus.OK),
    "11 reused": _Logged(
        lambda _w, lobby: (_draft(lobby.client), _draft(lobby.client))[1], HTTPStatus.OK
    ),
    "11 expired": _Logged(_expired_draft, HTTPStatus.OK),
    "12 two admins": _Logged(_second_admins_draft, HTTPStatus.OK),
    "13 reachable": _Logged(_posting(), HTTPStatus.OK),
    **{
        f"14 {failure.value}": _Logged(
            _posting(),
            HTTPStatus.OK,
            ConnectionCheckResult(failure=failure, commit=None, detail=_GIT_SAID),
        )
        for failure in SourceRunFailure
    },
    "15 own draft": _Logged(_own_draft_check, HTTPStatus.OK),
    "15 foreign draft": _Logged(_foreign_draft_check, HTTPStatus.OK),
    "15 stale draft": _Logged(_stale_draft_check, HTTPStatus.OK),
    "16 ssh for https": _Logged(
        _posting(access="ssh-deploy-key", secret=None), HTTPStatus.UNPROCESSABLE_ENTITY
    ),
    "16 user part": _Logged(_posting(url=_USER_PART), HTTPStatus.UNPROCESSABLE_ENTITY),
    "16 folder for https": _Logged(
        _posting(access="local-folder", secret=None), HTTPStatus.UNPROCESSABLE_ENTITY
    ),
    "17 radio token": _Logged(
        _posting(access="https"), HTTPStatus.UNPROCESSABLE_ENTITY
    ),
    "17 missing url": _Logged(
        lambda _w, lobby: lobby.client.post(
            _CHECK, json={"access": "https-token", "secret": _SENT}
        ),
        HTTPStatus.UNPROCESSABLE_ENTITY,
    ),
    "17 number": _Logged(
        _posting(secret=_SECRET_NUMBER), HTTPStatus.UNPROCESSABLE_ENTITY
    ),
}


@pytest.mark.parametrize("logged", _LOGGED.values(), ids=_LOGGED.keys())
def test_no_log_record_carries_the_token_or_a_private_half(
    logged: _Logged,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = a_world(_RecordingChecker(answer=logged.found))
    lobby = an_admin_over(world.decks)

    with caplog.at_level(logging.DEBUG):
        answer = logged.send(world, lobby)

    assert answer.status_code == logged.status
    assert_no_store(answer)
    assert caplog.records
    for value in (_SENT, _USER_PART, "user:pw", str(_SECRET_NUMBER)):
        assert value not in caplog.text
    for private_key in world.drafts.private_keys:
        assert private_key not in caplog.text


# 20
def test_a_checker_that_raises_is_the_plain_server_error_without_the_token() -> None:
    world = a_world(_CheckerThatBreaks())
    lobby = an_admin_over(world.decks)

    answer = without_raising(lobby).post(_CHECK, json=_checked_body())

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE
    assert_no_store(answer)
    assert_nothing_returned(answer, _SENT)


# 21
def test_the_generated_spec_types_the_refusal_and_both_forbidden_answers() -> None:
    spec = an_admin_over(decks_that_must_not_run()).app.openapi()
    check = spec["paths"][_CHECK]["post"]["responses"]
    refused = check["422"]["content"][PROBLEM_MEDIA_TYPE]["schema"]

    assert set(check["422"]["content"]) == {PROBLEM_MEDIA_TYPE, "application/json"}
    assert refused["properties"]["reason"]["enum"] == [r.value for r in SourceRefusal]
    assert refused["properties"]["type"]["const"] == _REFUSED_TYPE
    assert {"type", "title", "status", "reason"} <= set(refused["required"])
    for path in (_KEY_DRAFT, _CHECK):
        forbidden = spec["paths"][path]["post"]["responses"]["403"]
        assert set(forbidden["content"]) == {PROBLEM_MEDIA_TYPE}
