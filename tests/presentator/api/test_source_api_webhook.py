"""An admin renews a source's webhook secret through the JSON API, once."""

import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.api.sources import SOURCES
from presentator.application.decks import RESERVED_SOURCE_NAME
from presentator.contracts.decks import AccessKind
from tests.presentator.api.support.source_api_support import (
    A_SOURCE_NAME,
    AN_ADMIN_BY_COOKIE_AND_BEARER,
    ASKING_FOR_JSON,
    SAME_ORIGIN,
    WEBHOOK_RENEWAL,
    AdminReach,
    Instance,
    World,
    a_bearer,
    a_client_with_no_header_of_its_own,
    a_created_source,
    a_lobby_over,
    a_member_over,
    a_scene_that_must_not_run,
    a_world,
    an_admin_over,
    assert_no_store,
    assert_nothing_returned,
    assert_problem,
    calling_hook,
    decks_that_must_not_run,
    draft,
    renew_webhook,
    whole_answers_of,
    without_raising,
)
from tests.presentator.application.support.fakes import (
    LOCAL_MOUNT_EXAMPLE,
    FakeSourceStore,
)
from tests.support.lobby import ENGLISH, Lobby
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, Scene

_NAME = A_SOURCE_NAME
_SSH = "git@git.example.invalid:talks.git"
_FOLDER = f"{LOCAL_MOUNT_EXAMPLE}/talks.git"
_CREATED_AND_RENEWED = 2
_OWN_ORIGIN = {"sec-fetch-site": "same-site", "origin": "https://testserver"}
_MALFORMED_JSON = '{"webhook_secret": '
_NOT_FOUND_NAMES = ("unknown", RESERVED_SOURCE_NAME)
_SOURCES_BY_KIND = {
    AccessKind.SSH: {"url": _SSH, "access": AccessKind.SSH, "secret": None},
    AccessKind.FILE: {"url": _FOLDER, "access": AccessKind.FILE, "secret": None},
}
_CHOSEN = "a-value-the-caller-chose-for-the-secret"


class _RemovedWhileRenewing(FakeSourceStore):
    """A store whose row is deleted between the owner's lookup and its write."""

    def put_hook_secret_hash(self, name: str, digest: bytes) -> bool:
        self.sources = []
        return super().put_hook_secret_hash(name, digest)


class _FailsOnTheFirstHashWrite(FakeSourceStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def put_hook_secret_hash(self, name: str, digest: bytes) -> bool:
        if not self.failed:
            self.failed = True
            raise RuntimeError(name)
        return super().put_hook_secret_hash(name, digest)


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene_that_must_not_run(tmp_path)


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
def test_a_signed_out_caller_meets_webauths_answer_not_a_problem() -> None:
    lobby = a_lobby_over(decks_that_must_not_run())

    asking_json = renew_webhook(lobby.client, _NAME, ASKING_FOR_JSON)
    asking_html = renew_webhook(lobby.client, _NAME)

    assert asking_json.status_code == HTTPStatus.UNAUTHORIZED
    assert asking_html.status_code == HTTPStatus.FOUND
    for answer in (asking_json, asking_html):
        assert answer.headers.get("content-type") != PROBLEM_MEDIA_TYPE
        assert_no_store(answer)


# 2
def test_a_member_is_refused_with_a_problem() -> None:
    lobby = a_member_over(decks_that_must_not_run())

    answer = renew_webhook(lobby.client, _NAME)

    assert_problem(answer, HTTPStatus.FORBIDDEN)


# 3
def test_a_members_bearer_is_refused_with_a_problem(scene: Scene) -> None:
    answer = renew_webhook(
        a_client_with_no_header_of_its_own(scene), _NAME, a_bearer(scene, MEMBER)
    )

    assert_problem(answer, HTTPStatus.FORBIDDEN)


# 4
def test_a_members_bearer_beside_an_admin_cookie_carries_the_members_rights(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = renew_webhook(browser, _NAME, {**a_bearer(scene, MEMBER), **SAME_ORIGIN})

    assert_problem(answer, HTTPStatus.FORBIDDEN)


# 5
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(scene: Scene) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = renew_webhook(
        browser, _NAME, {"authorization": "Bearer nonsense", **SAME_ORIGIN}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert_no_store(answer)


# 6
@_NOT_SENT_BY_THE_SAME_ORIGIN
def test_the_origin_check_refuses_the_renewal_before_any_port_is_touched(
    headers: dict[str, str],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run(), headers={"accept": "text/html"})

    answer = renew_webhook(lobby.client, _NAME, headers)

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert_no_store(answer)


# 7
def test_a_same_site_renewal_with_the_own_origin_is_taken() -> None:
    lobby = an_admin_over(a_world().decks, headers={"accept": "text/html"})
    a_created_source(lobby.client, headers=_OWN_ORIGIN)

    answer = renew_webhook(lobby.client, _NAME, _OWN_ORIGIN)

    assert answer.status_code == HTTPStatus.OK
    assert_no_store(answer)


# 8, 9
@AN_ADMIN_BY_COOKIE_AND_BEARER
@pytest.mark.parametrize(
    ("body", "content"),
    [
        (None, None),
        ({"webhook_secret": _CHOSEN}, None),
        (None, _MALFORMED_JSON),
    ],
    ids=["no-body", "a-chosen-secret", "malformed-json"],
)
def test_an_admin_renews_and_the_answer_is_the_minted_value_alone(
    reach: AdminReach, tmp_path: Path, body: object, content: str | None
) -> None:
    world = a_world()
    instance = reach(world, tmp_path)
    a_created_source(instance.client, headers=instance.headers)
    headers = instance.headers | (
        {} if content is None else {"content-type": "application/json"}
    )

    answer = renew_webhook(instance.client, _NAME, headers, content=content, body=body)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == {"webhook_secret": world.webhook_secrets.handed_out[-1]}
    assert "location" not in answer.headers
    assert_no_store(answer)
    assert_nothing_returned(answer, _CHOSEN)
    for header in answer.headers.values():
        assert world.webhook_secrets.handed_out[-1] not in header


# 10
def test_the_hook_takes_the_renewed_secret_and_refuses_the_old_and_an_altered_one() -> (
    None
):
    world = a_world()
    client = an_admin_over(world.decks).client
    created = a_created_source(client)

    renewed = renew_webhook(client, _NAME).json()["webhook_secret"]

    assert (
        calling_hook(client, _NAME, offered=renewed).status_code
        == HTTPStatus.NO_CONTENT
    )
    assert (
        calling_hook(client, _NAME, offered=created).status_code == HTTPStatus.NOT_FOUND
    )
    assert (
        calling_hook(client, _NAME, offered=renewed + "x").status_code
        == HTTPStatus.NOT_FOUND
    )


# 11
def test_a_lost_answer_is_recovered_by_renewing_again() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    a_created_source(client)
    renew_webhook(client, _NAME)

    second = renew_webhook(client, _NAME)

    _, lost, kept = world.webhook_secrets.handed_out
    assert second.json() == {"webhook_secret": kept}
    assert calling_hook(client, _NAME, offered=lost).status_code == HTTPStatus.NOT_FOUND
    assert (
        calling_hook(client, _NAME, offered=kept).status_code == HTTPStatus.NO_CONTENT
    )


def _a_created_source_of(client: TestClient, kind: AccessKind) -> str:
    changes: dict[str, object] = dict(_SOURCES_BY_KIND[kind])
    if kind is AccessKind.SSH:
        changes["key_draft_id"] = draft(client).json()["id"]
    return a_created_source(client, changes)


# 12
@pytest.mark.parametrize("kind", list(_SOURCES_BY_KIND))
def test_every_kind_of_source_has_a_webhook_to_renew(kind: AccessKind) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    _a_created_source_of(client, kind)

    answer = renew_webhook(client, _NAME)

    assert answer.status_code == HTTPStatus.OK
    assert calling_hook(
        client, _NAME, offered=answer.json()["webhook_secret"]
    ).status_code == (HTTPStatus.NO_CONTENT)


# 13
@pytest.mark.parametrize("name", _NOT_FOUND_NAMES)
def test_an_unknown_name_is_a_problem_and_changes_nothing(name: str) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    a_created_source(client)
    hashes = dict(world.store.hashes)

    answer = renew_webhook(client, name)

    assert_problem(answer, HTTPStatus.NOT_FOUND, *world.webhook_secrets.handed_out)
    assert world.store.hashes == hashes


# 14
def test_a_name_that_is_not_one_segment_is_the_frameworks_404() -> None:
    client = an_admin_over(a_world().decks).client

    answer = client.post(WEBHOOK_RENEWAL.format(name="a/b"))

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert_no_store(answer)


# 15
def test_a_source_gone_when_the_owner_writes_is_a_problem_with_no_secret() -> None:
    world = a_world()
    store = _RemovedWhileRenewing(key_drafts=world.drafts)
    client = an_admin_over(replace(world.decks, sources=store)).client
    a_created_source(client)

    answer = renew_webhook(client, _NAME)

    assert_problem(answer, HTTPStatus.NOT_FOUND, *world.webhook_secrets.handed_out)
    assert len(world.webhook_secrets.handed_out) == _CREATED_AND_RENEWED


# 16
@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_the_route_answers_no_other_method(method: str) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    a_created_source(client)

    answer = getattr(client, method)(
        WEBHOOK_RENEWAL.format(name=_NAME), headers=SAME_ORIGIN
    )

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert_no_store(answer)
    assert_nothing_returned(answer, *world.webhook_secrets.handed_out)


# 17
@AN_ADMIN_BY_COOKIE_AND_BEARER
def test_no_get_carries_the_renewed_or_the_old_secret(
    reach: AdminReach, tmp_path: Path
) -> None:
    world = a_world()
    instance: Instance = reach(world, tmp_path)
    a_created_source(instance.client, headers=instance.headers)
    renew_webhook(instance.client, _NAME, instance.headers)

    shown = whole_answers_of(instance, _NAME)

    assert len(world.webhook_secrets.handed_out) == _CREATED_AND_RENEWED
    for minted in world.webhook_secrets.handed_out:
        assert minted not in shown


# 18
def test_the_jinja_page_after_a_renewal_has_no_one_time_secret() -> None:
    world = a_world()
    lobby = an_admin_over(world.decks)
    a_created_source(lobby.client)
    renew_webhook(lobby.client, _NAME)

    answer = lobby.client.get(f"{SOURCES}/{_NAME}")

    assert_no_store(answer)
    assert "data-hook-secret" not in answer.text
    assert ENGLISH.source_webhook_once not in answer.text
    assert ENGLISH.source_webhook_held_elsewhere not in answer.text
    assert_nothing_returned(answer, *world.webhook_secrets.handed_out)


# 19
def _renewed_in_the_log(_world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client)
    return renew_webhook(lobby.client, _NAME)


def _same_site_in_the_log(_world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client, headers=_OWN_ORIGIN)
    return renew_webhook(lobby.client, _NAME, _OWN_ORIGIN)


def _chosen_secret_in_the_log(_world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client)
    return renew_webhook(lobby.client, _NAME, body={"webhook_secret": _CHOSEN})


def _malformed_json_in_the_log(_world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client)
    return renew_webhook(
        lobby.client,
        _NAME,
        {"content-type": "application/json"},
        content=_MALFORMED_JSON,
    )


def _hook_after_renewing_in_the_log(_world: World, lobby: Lobby) -> Response:
    created = a_created_source(lobby.client)
    renewed = renew_webhook(lobby.client, _NAME).json()["webhook_secret"]
    calling_hook(lobby.client, _NAME, offered=renewed)
    calling_hook(lobby.client, _NAME, offered=created)
    return calling_hook(lobby.client, _NAME, offered=renewed + "x")


def _renewed_twice_in_the_log(_world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client)
    renew_webhook(lobby.client, _NAME)
    return renew_webhook(lobby.client, _NAME)


def _kind_in_the_log(kind: AccessKind, _world: World, lobby: Lobby) -> Response:
    _a_created_source_of(lobby.client, kind)
    return renew_webhook(lobby.client, _NAME)


def _not_found_in_the_log(name: str, _world: World, lobby: Lobby) -> Response:
    a_created_source(lobby.client)
    return renew_webhook(lobby.client, name)


def _not_one_segment_in_the_log(_world: World, lobby: Lobby) -> Response:
    return lobby.client.post(WEBHOOK_RENEWAL.format(name="a/b"))


def _vanished_in_the_log(world: World, _lobby: Lobby) -> Response:
    store = _RemovedWhileRenewing(key_drafts=world.drafts)
    lobby = an_admin_over(replace(world.decks, sources=store))
    a_created_source(lobby.client)
    return renew_webhook(lobby.client, _NAME)


def _failed_write_in_the_log(world: World, _lobby: Lobby) -> Response:
    lobby = an_admin_over(replace(world.decks, sources=_FailsOnTheFirstHashWrite()))
    a_created_source(lobby.client)
    return renew_webhook(without_raising(lobby), _NAME)


def _retry_after_a_failed_write_in_the_log(world: World, _lobby: Lobby) -> Response:
    lobby = an_admin_over(replace(world.decks, sources=_FailsOnTheFirstHashWrite()))
    a_created_source(lobby.client)
    renew_webhook(without_raising(lobby), _NAME)
    return renew_webhook(without_raising(lobby), _NAME)


@pytest.mark.parametrize(
    ("send", "status"),
    [
        (_renewed_in_the_log, HTTPStatus.OK),
        (_same_site_in_the_log, HTTPStatus.OK),
        (_chosen_secret_in_the_log, HTTPStatus.OK),
        (_malformed_json_in_the_log, HTTPStatus.OK),
        (_hook_after_renewing_in_the_log, HTTPStatus.NOT_FOUND),
        (_renewed_twice_in_the_log, HTTPStatus.OK),
        *(
            (partial(_kind_in_the_log, kind), HTTPStatus.OK)
            for kind in _SOURCES_BY_KIND
        ),
        *(
            (partial(_not_found_in_the_log, name), HTTPStatus.NOT_FOUND)
            for name in _NOT_FOUND_NAMES
        ),
        (_not_one_segment_in_the_log, HTTPStatus.NOT_FOUND),
        (_vanished_in_the_log, HTTPStatus.NOT_FOUND),
        (_failed_write_in_the_log, HTTPStatus.INTERNAL_SERVER_ERROR),
        (_retry_after_a_failed_write_in_the_log, HTTPStatus.OK),
    ],
    ids=[
        "renewed",
        "same-site",
        "chosen-secret",
        "malformed-json",
        "hook",
        "renewed-twice",
        *_SOURCES_BY_KIND,
        *_NOT_FOUND_NAMES,
        "not-one-segment",
        "vanished",
        "failed-write",
        "retry-after-failed-write",
    ],
)
def test_no_log_record_and_no_error_answer_carries_a_minted_secret(
    send: Callable[[World, Lobby], Response],
    status: HTTPStatus,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world = a_world()
    lobby = an_admin_over(world.decks)

    with caplog.at_level(logging.DEBUG):
        answer = send(world, lobby)

    assert answer.status_code == status
    assert caplog.records
    assert_no_store(answer)
    assert _CHOSEN not in caplog.text
    for minted in world.webhook_secrets.handed_out:
        assert minted not in caplog.text
        if status != HTTPStatus.OK:
            assert_nothing_returned(answer, minted)


# 20
def test_a_failed_hash_write_keeps_the_old_secret_and_renewing_again_recovers() -> None:
    world = a_world()
    store = _FailsOnTheFirstHashWrite()
    lobby = an_admin_over(replace(world.decks, sources=store))
    created = a_created_source(lobby.client)
    quiet = without_raising(lobby)

    failed = renew_webhook(quiet, _NAME)
    still_the_old = calling_hook(lobby.client, _NAME, offered=created)
    renewed = renew_webhook(quiet, _NAME)

    _, lost, kept = world.webhook_secrets.handed_out
    assert failed.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert failed.headers["content-type"] != PROBLEM_MEDIA_TYPE
    assert_no_store(failed)
    assert_nothing_returned(failed, lost)
    assert still_the_old.status_code == HTTPStatus.NO_CONTENT
    assert renewed.json() == {"webhook_secret": kept}
    assert (
        calling_hook(lobby.client, _NAME, offered=created).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert (
        calling_hook(lobby.client, _NAME, offered=lost).status_code
        == HTTPStatus.NOT_FOUND
    )
    assert (
        calling_hook(lobby.client, _NAME, offered=kept).status_code
        == HTTPStatus.NO_CONTENT
    )


# 21
def test_the_generated_spec_types_the_renewal_without_a_request_body() -> None:
    spec = an_admin_over(decks_that_must_not_run()).app.openapi()
    route = spec["paths"][WEBHOOK_RENEWAL]["post"]
    renewed = spec["components"]["schemas"]["WebhookSecretView"]

    assert "requestBody" not in route
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WebhookSecretView"
    }
    assert set(renewed["properties"]) == {"webhook_secret"}
    for refused in ("403", "404"):
        assert set(route["responses"][refused]["content"]) == {PROBLEM_MEDIA_TYPE}
