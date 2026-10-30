"""An admin creates a source through the JSON API, and its webhook secret comes once."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import JsonValue

from presentator.api.hooks import hook_address
from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.api.sources import SOURCES
from presentator.application.decks import (
    DRAFT_MAX_AGE,
    RESERVED_SOURCE_NAME,
    SourceRefusal,
)
from presentator.contracts.decks import Source, SourcePoll, SourceState, SourceWrite
from presentator.contracts.models import Role
from tests.presentator.api.support.source_api_support import (
    A_SOURCE_NAME,
    AN_ADMIN_BY_COOKIE_AND_BEARER,
    ASKING_FOR_JSON,
    CHECK,
    HTTPS,
    LIST,
    PAGE,
    SAME_ORIGIN,
    SENT,
    AdminReach,
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
    assert_validation_answer,
    calling_hook,
    checked_body,
    decks_that_must_not_run,
    draft,
    renew_webhook,
    whole_answers_of,
    without_raising,
)
from tests.presentator.application.support.fakes import (
    LOCAL_MOUNT_EXAMPLE,
    FakeDeckFolders,
    FakeLocalMount,
    FakeSourceStore,
)
from tests.support.lobby import ENGLISH, Lobby
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, Scene

_NAME = A_SOURCE_NAME
_OTHER_URL = "https://git.example.invalid/other.git"
_SSH = "git@git.example.invalid:talks.git"
_FOLDER = f"{LOCAL_MOUNT_EXAMPLE}/talks.git"
_NO_PROOF = ""
_NOT_A_NAME = "not a name"
_NOT_THE_MOUNT = "/etc/talks.git"
_USER_PART = "https://user:pw@git.example.invalid/talks.git"


class _RejectingStore(FakeSourceStore):
    """A store whose write finds the name taken by a concurrent create."""

    def add(self, write: SourceWrite) -> Source | None:
        return None


class _BreakingStore(FakeSourceStore):
    def add(self, write: SourceWrite) -> Source | None:
        raise RuntimeError(write.access_secret)


class _BreakingFolders(FakeDeckFolders):
    def folders(self, source: Source) -> SourcePoll:
        raise RuntimeError(source.url)


class _BreakingFoldersOnce(FakeDeckFolders):
    """Folders whose first fetch raises, as a mirror that was down for a moment."""

    broken = False

    def folders(self, source: Source) -> SourcePoll:
        if not self.broken:
            self.broken = True
            raise RuntimeError(source.url)
        return super().folders(source)


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene_that_must_not_run(tmp_path)


def create(
    client: TestClient,
    headers: dict[str, str] | None = None,
    /,
    **changes: object,
) -> Response:
    """Create after the check of the same values, as the page must."""
    body = checked_body(**{"name": _NAME} | changes)
    if "fingerprint" not in body:
        checked = client.post(CHECK, json=body, headers=headers).json()
        body["fingerprint"] = checked.get("fingerprint") or _NO_PROOF
    return client.post(LIST, json=body, headers=headers)


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

    asking_json = create(lobby.client, ASKING_FOR_JSON, fingerprint=_NO_PROOF)
    asking_html = create(lobby.client, None, fingerprint=_NO_PROOF)

    assert asking_json.status_code == HTTPStatus.UNAUTHORIZED
    assert asking_html.status_code == HTTPStatus.FOUND
    for answer in (asking_json, asking_html):
        assert answer.headers.get("content-type") != PROBLEM_MEDIA_TYPE
        assert_no_store(answer)
        assert_nothing_returned(answer, SENT)


# 2
def test_a_member_is_refused_with_a_problem() -> None:
    lobby = a_member_over(decks_that_must_not_run())

    answer = create(lobby.client, fingerprint=_NO_PROOF)

    assert_problem(answer, HTTPStatus.FORBIDDEN, SENT)


# 3
def test_a_members_bearer_is_refused_with_a_problem(scene: Scene) -> None:
    answer = create(
        a_client_with_no_header_of_its_own(scene),
        a_bearer(scene, MEMBER),
        fingerprint=_NO_PROOF,
    )

    assert_problem(answer, HTTPStatus.FORBIDDEN, SENT)


# 4
def test_a_members_bearer_beside_an_admin_cookie_carries_the_members_rights(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = create(
        browser, {**a_bearer(scene, MEMBER), **SAME_ORIGIN}, fingerprint=_NO_PROOF
    )

    assert_problem(answer, HTTPStatus.FORBIDDEN, SENT)


# 5
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(scene: Scene) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = create(
        browser,
        {"authorization": "Bearer nonsense", **SAME_ORIGIN},
        fingerprint=_NO_PROOF,
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert_no_store(answer)
    assert_nothing_returned(answer, SENT)


# 6
@_NOT_SENT_BY_THE_SAME_ORIGIN
def test_the_origin_check_refuses_the_create_before_any_port_is_touched(
    headers: dict[str, str],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run(), headers={"accept": "text/html"})

    answer = create(lobby.client, headers, fingerprint=_NO_PROOF)

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert_no_store(answer)
    assert_nothing_returned(answer, SENT)


# 7
def test_a_same_site_create_with_the_own_origin_is_taken() -> None:
    world = a_world()
    lobby = an_admin_over(world.decks, headers={"accept": "text/html"})

    answer = create(
        lobby.client,
        {"sec-fetch-site": "same-site", "origin": "https://testserver"},
    )

    assert answer.status_code == HTTPStatus.CREATED
    assert_no_store(answer)
    assert [source.name for source in world.store.sources] == [_NAME]


# 8, 9
@AN_ADMIN_BY_COOKIE_AND_BEARER
def test_an_admin_creates_a_source_and_is_its_owner(
    reach: AdminReach, tmp_path: Path
) -> None:
    world = a_world()
    instance = reach(world, tmp_path)

    answer = create(instance.client, instance.headers)

    body = answer.json()
    (stored,) = world.store.sources
    shown = instance.client.get(
        PAGE.format(name=_NAME), headers={**instance.headers, **ASKING_FOR_JSON}
    ).json()
    assert answer.status_code == HTTPStatus.CREATED
    assert set(body) == {"name", "hook_url", "webhook_secret"}
    assert body["name"] == _NAME
    assert body["hook_url"] == shown["hook_url"]
    assert body["hook_url"].endswith(hook_address(_NAME))
    assert answer.headers["location"] == PAGE.format(name=_NAME)
    assert stored.owner_id == instance.person_id
    assert len(shown["runs"]) == 1
    assert shown["state"] == SourceState.REACHABLE
    assert_no_store(answer)
    for address in (answer.headers["location"], body["hook_url"]):
        assert SENT not in address
        assert body["webhook_secret"] not in address
    assert_nothing_returned(answer, SENT, *world.drafts.private_keys)


# 10
def test_the_hook_takes_the_returned_secret_and_refuses_a_wrong_one() -> None:
    client = an_admin_over(a_world().decks).client
    webhook_secret = a_created_source(client)

    taken = calling_hook(client, _NAME, offered=webhook_secret)
    wrong = calling_hook(client, _NAME, offered=webhook_secret + "x")

    assert taken.status_code == HTTPStatus.NO_CONTENT
    assert wrong.status_code == HTTPStatus.NOT_FOUND
    assert_no_store(taken)
    assert_no_store(wrong)


# 11, 19
@AN_ADMIN_BY_COOKIE_AND_BEARER
def test_no_get_carries_the_webhook_secret_or_the_token(
    reach: AdminReach, tmp_path: Path
) -> None:
    instance = reach(a_world(), tmp_path)
    created = create(instance.client, instance.headers)

    shown = whole_answers_of(instance, _NAME)

    assert created.status_code == HTTPStatus.CREATED
    assert created.json()["webhook_secret"] not in shown
    assert SENT not in shown
    assert (
        instance.client.get(LIST, headers=instance.headers).json()["sources"][0]["name"]
        == _NAME
    )


# 12
def test_the_jinja_page_of_an_api_created_source_has_no_one_time_secret() -> None:
    lobby = an_admin_over(a_world().decks)
    webhook_secret = a_created_source(lobby.client)

    answer = lobby.client.get(f"{SOURCES}/{_NAME}")
    page = answer.text

    assert_no_store(answer)
    assert webhook_secret not in page
    assert "data-hook-secret" not in page
    assert ENGLISH.source_webhook_once not in page
    assert ENGLISH.source_webhook_held_elsewhere not in page


@dataclass(frozen=True, slots=True)
class _Refused:
    reason: SourceRefusal
    changes: dict[str, object]


_REFUSALS = {
    "malformed name": _Refused(SourceRefusal.MALFORMED_NAME, {"name": _NOT_A_NAME}),
    "reserved name": _Refused(
        SourceRefusal.MALFORMED_NAME, {"name": RESERVED_SOURCE_NAME}
    ),
    "access mismatch": _Refused(
        SourceRefusal.ACCESS_MISMATCH, {"access": "ssh-deploy-key", "secret": None}
    ),
    "missing secret": _Refused(SourceRefusal.BLANK_ACCESS, {"secret": None}),
    "not checked": _Refused(
        SourceRefusal.NOT_CHECKED, {"fingerprint": "a-proof-for-nothing"}
    ),
    "credential not allowed": _Refused(
        SourceRefusal.CREDENTIAL_NOT_ALLOWED, {"url": _FOLDER, "access": "local-folder"}
    ),
    "outside the mount": _Refused(
        SourceRefusal.OUTSIDE_MOUNT,
        {"url": _NOT_THE_MOUNT, "access": "local-folder", "secret": None},
    ),
    "deploy key unavailable": _Refused(
        SourceRefusal.DEPLOY_KEY_UNAVAILABLE,
        {"url": _SSH, "access": "ssh-deploy-key", "secret": None, "key_draft_id": "x"},
    ),
}


# 13
@pytest.mark.parametrize("refused", _REFUSALS.values(), ids=_REFUSALS.keys())
def test_a_refused_create_names_its_reason_and_makes_nothing(refused: _Refused) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    answer = create(client, **refused.changes)

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == refused.reason
    assert world.store.sources == []
    assert world.store.hashes == {}
    assert (
        calling_hook(
            client, str(refused.changes.get("name", _NAME)), offered=SENT
        ).status_code
        == HTTPStatus.NOT_FOUND
    )


def test_the_fingerprint_of_another_address_is_not_a_check_of_this_one() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    other_proof = client.post(CHECK, json=checked_body(url=_OTHER_URL)).json()

    answer = create(client, fingerprint=other_proof["fingerprint"])

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.NOT_CHECKED
    assert world.store.sources == []
    assert world.store.hashes == {}
    assert calling_hook(client, _NAME, offered=SENT).status_code == HTTPStatus.NOT_FOUND


@pytest.mark.parametrize(
    ("reason", "second"),
    [
        (SourceRefusal.DUPLICATE_NAME, {"url": _OTHER_URL}),
        (SourceRefusal.DUPLICATE_URL, {"name": "other"}),
    ],
    ids=["duplicate name", "duplicate url"],
)
def test_a_duplicate_is_refused_and_leaves_the_existing_source_as_it_was(
    reason: SourceRefusal, second: dict[str, object]
) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    webhook_secret = a_created_source(client)
    (before,) = world.store.sources

    answer = create(client, **second)

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == reason
    assert world.store.sources == [before]
    assert calling_hook(client, _NAME, offered=webhook_secret).status_code == (
        HTTPStatus.NO_CONTENT
    )
    assert calling_hook(client, _NAME, offered=SENT).status_code == HTTPStatus.NOT_FOUND
    assert (
        calling_hook(client, str(second.get("name", _NAME)), offered=SENT).status_code
        == HTTPStatus.NOT_FOUND
    )


# 14
@pytest.mark.parametrize("which", ["foreign", "stale"])
def test_a_foreign_or_stale_draft_is_refused_and_the_next_draft_is_fresh(
    which: str,
) -> None:
    world = a_world()
    admin = an_admin_over(world.decks)
    own = draft(admin.client).json()["id"]
    other = admin.another_browser()
    other.sign_in(username="second-admin", role=Role.ADMIN)
    offered = draft(other.client).json()["id"] if which == "foreign" else own
    if which == "stale":
        world.clock.advance(DRAFT_MAX_AGE + timedelta(seconds=1))

    answer = create(
        admin.client,
        fingerprint=_NO_PROOF,
        url=_SSH,
        access="ssh-deploy-key",
        secret=None,
        key_draft_id=offered,
    )

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.DEPLOY_KEY_UNAVAILABLE
    assert world.store.sources == []
    assert draft(admin.client).json()["id"] not in {own, offered}


# 15
def test_an_ssh_create_binds_the_own_draft_and_the_next_draft_is_new() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    drafted = draft(client).json()

    answer = create(
        client,
        url=_SSH,
        access="ssh-deploy-key",
        secret=None,
        key_draft_id=drafted["id"],
    )

    (stored,) = world.store.sources
    assert answer.status_code == HTTPStatus.CREATED
    assert stored.public_key == drafted["public_key"]
    assert draft(client).json()["id"] != drafted["id"]
    assert_no_store(answer)
    assert_nothing_returned(answer, *world.drafts.private_keys)


# 16
def test_a_local_folder_under_the_mount_is_stored_by_its_canonical_address() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    answer = create(client, url=_FOLDER, access="local-folder", secret=None)

    (stored,) = world.store.sources
    assert answer.status_code == HTTPStatus.CREATED
    assert stored.url == str(FakeLocalMount().canonical_repository(_FOLDER))
    assert_no_store(answer)


# 17
def test_a_store_that_rejects_the_row_makes_no_source_and_keeps_no_secret() -> None:
    world = a_world()
    store = _RejectingStore()
    client = an_admin_over(replace(world.decks, sources=store)).client

    answer = create(client)

    assert world.webhook_secrets.handed_out
    assert_problem(
        answer,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        SENT,
        "webhook_secret",
        *world.webhook_secrets.handed_out,
    )
    assert answer.json()["reason"] == SourceRefusal.DUPLICATE_NAME
    assert store.sources == []
    assert store.hashes == {}


# 18
@pytest.mark.parametrize(
    "body",
    [
        checked_body(name=_NAME, fingerprint=_NO_PROOF, access="https"),
        {"name": _NAME, "access": "https-token", "secret": SENT, "fingerprint": "x"},
        {"name": _NAME, "url": HTTPS, "access": "https-token", "secret": SENT},
        checked_body(name=_NAME, fingerprint=_NO_PROOF, secret=12345),
    ],
    ids=["radio-token", "url-missing", "fingerprint-missing", "secret-a-number"],
)
def test_a_malformed_create_is_the_validation_answer_without_what_was_sent(
    body: dict[str, object],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run())

    answer = lobby.client.post(LIST, json=body)

    assert_validation_answer(answer, SENT, "12345")


# 19
def test_the_list_is_served_and_the_collection_has_no_other_methods() -> None:
    lobby = an_admin_over(a_world().decks)

    listed = lobby.client.get(LIST, headers=ASKING_FOR_JSON)
    others = [lobby.client.put(LIST, headers=SAME_ORIGIN), lobby.client.delete(LIST)]

    assert listed.status_code == HTTPStatus.OK
    assert [other.status_code for other in others] == [
        HTTPStatus.METHOD_NOT_ALLOWED
    ] * 2
    for answer in (listed, *others):
        assert_no_store(answer)


# 20
def _https_in_the_log(_world: World, lobby: Lobby) -> Response:
    return create(lobby.client)


def _refusal_in_the_log(_world: World, lobby: Lobby) -> Response:
    return create(lobby.client, url=_USER_PART)


def _refused_in_the_log(
    changes: dict[str, object], _world: World, lobby: Lobby
) -> Response:
    return create(lobby.client, **changes)


def _other_urls_fingerprint_in_the_log(_world: World, lobby: Lobby) -> Response:
    other_proof = lobby.client.post(CHECK, json=checked_body(url=_OTHER_URL)).json()
    return create(lobby.client, fingerprint=other_proof["fingerprint"])


def _duplicate_in_the_log(
    changes: dict[str, object], _world: World, lobby: Lobby
) -> Response:
    a_created_source(lobby.client)
    return create(lobby.client, **changes)


def _ssh_in_the_log(_world: World, lobby: Lobby) -> Response:
    drafted = draft(lobby.client).json()
    return create(
        lobby.client,
        url=_SSH,
        access="ssh-deploy-key",
        secret=None,
        key_draft_id=drafted["id"],
    )


def _rejected_row_in_the_log(world: World, _lobby: Lobby) -> Response:
    lobby = an_admin_over(replace(world.decks, sources=_RejectingStore()))
    return create(lobby.client)


def _store_raising_in_the_log(world: World, _lobby: Lobby) -> Response:
    lobby = an_admin_over(replace(world.decks, sources=_BreakingStore()))
    return create(without_raising(lobby))


def _first_fetch_raising_in_the_log(world: World, _lobby: Lobby) -> Response:
    lobby = an_admin_over(replace(world.decks, folders=_BreakingFolders()))
    return create(without_raising(lobby))


@pytest.mark.parametrize(
    ("send", "status"),
    [
        (_https_in_the_log, HTTPStatus.CREATED),
        (_rejected_row_in_the_log, HTTPStatus.UNPROCESSABLE_ENTITY),
        (_store_raising_in_the_log, HTTPStatus.INTERNAL_SERVER_ERROR),
        (_first_fetch_raising_in_the_log, HTTPStatus.INTERNAL_SERVER_ERROR),
        (_ssh_in_the_log, HTTPStatus.CREATED),
        (_refusal_in_the_log, HTTPStatus.UNPROCESSABLE_ENTITY),
        (_other_urls_fingerprint_in_the_log, HTTPStatus.UNPROCESSABLE_ENTITY),
        (
            partial(_duplicate_in_the_log, {"url": _OTHER_URL}),
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        (
            partial(_duplicate_in_the_log, {"name": "other"}),
            HTTPStatus.UNPROCESSABLE_ENTITY,
        ),
        *(
            (
                partial(_refused_in_the_log, refused.changes),
                HTTPStatus.UNPROCESSABLE_ENTITY,
            )
            for refused in _REFUSALS.values()
        ),
    ],
    ids=[
        "https",
        "rejected row",
        "store raises",
        "first fetch raises",
        "ssh",
        "user-part",
        "other-url fingerprint",
        "duplicate name",
        "duplicate url",
        *_REFUSALS,
    ],
)
def test_no_log_record_carries_the_token_the_webhook_secret_or_a_private_half(
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
    assert SENT not in caplog.text
    assert "user:pw" not in caplog.text
    for minted in world.webhook_secrets.handed_out:
        assert minted not in caplog.text
        if answer.status_code != HTTPStatus.CREATED:
            assert_nothing_returned(answer, minted)
    for private_key in world.drafts.private_keys:
        assert private_key not in caplog.text


# 21
def test_a_store_that_raises_is_the_plain_server_error_with_no_source() -> None:
    world = a_world()
    store = _BreakingStore()
    lobby = an_admin_over(replace(world.decks, sources=store))

    answer = without_raising(lobby).post(
        LIST, json=checked_body(name=_NAME, fingerprint=_proof_of(lobby))
    )

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE
    assert_no_store(answer)
    assert world.webhook_secrets.handed_out
    assert_nothing_returned(
        answer, SENT, "webhook_secret", *world.webhook_secrets.handed_out
    )
    assert store.sources == []


def _proof_of(lobby: Lobby) -> str:
    checked = lobby.client.post(CHECK, json=checked_body()).json()
    return checked["fingerprint"]


def test_a_first_fetch_that_raises_leaves_the_source_and_renewal_recovers_it() -> None:
    world = a_world()
    decks = replace(world.decks, folders=_BreakingFoldersOnce())
    lobby = an_admin_over(decks)

    answer = without_raising(lobby).post(
        LIST, json=checked_body(name=_NAME, fingerprint=_proof_of(lobby))
    )
    renewed = renew_webhook(lobby.client, _NAME)

    (lost, kept) = world.webhook_secrets.handed_out
    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != PROBLEM_MEDIA_TYPE
    assert_no_store(answer)
    assert_nothing_returned(answer, SENT, "webhook_secret", lost)
    assert [source.name for source in world.store.sources] == [_NAME]
    assert renewed.json() == {"webhook_secret": kept}
    assert_no_store(renewed)
    taken = calling_hook(lobby.client, _NAME, offered=kept)
    refused = calling_hook(lobby.client, _NAME, offered=lost)
    assert taken.status_code == HTTPStatus.NO_CONTENT
    assert refused.status_code == HTTPStatus.NOT_FOUND
    assert_no_store(taken)
    assert_no_store(refused)


# 22
def test_the_generated_spec_types_the_created_answer_and_its_refusals() -> None:
    spec = an_admin_over(decks_that_must_not_run()).app.openapi()
    route = spec["paths"][LIST]["post"]["responses"]
    refused = route["422"]["content"][PROBLEM_MEDIA_TYPE]["schema"]
    created = spec["components"]["schemas"]["CreatedSourceView"]

    assert route["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CreatedSourceView"
    }
    assert set(created["properties"]) == {"name", "hook_url", "webhook_secret"}
    assert set(route["422"]["content"]) == {PROBLEM_MEDIA_TYPE, "application/json"}
    assert refused["properties"]["reason"]["enum"] == [r.value for r in SourceRefusal]
    assert set(route["403"]["content"]) == {PROBLEM_MEDIA_TYPE}
    for ref in _refs(spec):
        _target_of(spec, ref)


def _target_of(spec: JsonValue, ref: str) -> JsonValue:
    """The part of the spec a local reference names; a missing one raises."""
    target = spec
    for segment in ref.removeprefix("#/").split("/"):
        assert isinstance(target, dict)
        target = target[segment]
    return target


def _refs(document: JsonValue) -> list[str]:
    if isinstance(document, dict):
        found = [str(document["$ref"])] if "$ref" in document else []
        return found + [ref for each in document.values() for ref in _refs(each)]
    if isinstance(document, list):
        return [ref for each in document for ref in _refs(each)]
    return []
