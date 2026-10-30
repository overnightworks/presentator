"""An admin renews a source's access through the JSON API, and nothing returns it."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.openapi.utils import (
    validation_error_definition,
    validation_error_response_definition,
)
from httpx2 import Response

from presentator.api.problems import PROBLEM_MEDIA_TYPE
from presentator.contracts.decks import Source
from tests.presentator.api.support.source_api_support import (
    ACCESS,
    ASKING_FOR_JSON,
    LIST,
    PAGE,
    SAME_ORIGIN,
    Instance,
    a_bearer,
    a_client_with_no_header_of_its_own,
    a_lobby_over,
    a_member_over,
    a_scene_that_must_not_run,
    a_source_store,
    an_admin_bearer_instance,
    an_admin_cookie_instance,
    an_admin_over,
    assert_no_store,
    assert_nothing_returned,
    assert_problem,
    assert_validation_answer,
    decks_over,
    decks_that_must_not_run,
    renew,
    whole_answers_of,
    without_raising,
)
from tests.presentator.application.support.fakes import (
    LOCAL_MOUNT_EXAMPLE,
    FakeSourceStore,
)
from tests.support.lobby import ADMIN
from tests.support.sign_in_support import ADMIN as ADMIN_GROUPS
from tests.support.sign_in_support import MEMBER, Scene

_OLD = "the-token-this-scenario-replaces"
_NEW = "the-token-this-scenario-sends"
_HTTPS = "https://git.example.invalid/talks.git"
_SSH = "git@git.example.invalid:talks.git"
_LOCAL = f"{LOCAL_MOUNT_EXAMPLE}/talks.git"
_NAME = "decks"
_SEEN_BY_NOBODY = pytest.mark.parametrize(
    "body", [{"secret": _NEW}, {"secret": 12345}, {"secret": None}]
)


def a_source(url: str) -> Source:
    return Source(id="the-source", name=_NAME, url=url, ref="main", owner_id=ADMIN)


def an_https_store() -> FakeSourceStore:
    return a_source_store(a_source(_HTTPS), secret=_OLD)


def _admin_cookie(store: FakeSourceStore, _tmp_path: Path) -> Instance:
    return an_admin_cookie_instance(decks_over(store), store)


def _admin_bearer(store: FakeSourceStore, tmp_path: Path) -> Instance:
    return an_admin_bearer_instance(decks_over(store), store, tmp_path)


@pytest.fixture(params=[_admin_cookie, _admin_bearer], ids=["cookie", "bearer"])
def an_admin(
    request: pytest.FixtureRequest, tmp_path: Path
) -> Callable[[FakeSourceStore], Instance]:
    return lambda store: request.param(store, tmp_path)


@pytest.fixture
def scene(tmp_path: Path) -> Scene:
    return a_scene_that_must_not_run(tmp_path)


# 1
def test_a_signed_out_caller_meets_webauths_answer_not_a_problem() -> None:
    lobby = a_lobby_over(decks_that_must_not_run())

    asking_json = renew(lobby.client, _NAME, {"secret": _NEW}, headers=ASKING_FOR_JSON)
    asking_html = renew(lobby.client, _NAME, {"secret": _NEW})

    assert asking_json.status_code == HTTPStatus.UNAUTHORIZED
    assert asking_html.status_code == HTTPStatus.FOUND
    for answer in (asking_json, asking_html):
        assert answer.headers.get("content-type") != "application/problem+json"
        assert_no_store(answer)
        assert_nothing_returned(answer, _OLD, _NEW)


# 2, 3
@_SEEN_BY_NOBODY
def test_a_member_is_refused_with_a_problem_whatever_the_body_says(
    body: dict[str, object],
) -> None:
    lobby = a_member_over(decks_that_must_not_run())

    assert_problem(renew(lobby.client, _NAME, body), HTTPStatus.FORBIDDEN, _OLD, _NEW)


# 4
def test_a_member_who_sends_no_json_meets_the_validation_answer_first() -> None:
    lobby = a_member_over(decks_that_must_not_run())

    answer = lobby.client.post(
        ACCESS.format(name=_NAME),
        content=_NEW,
        headers={"content-type": "application/json"},
    )

    assert_validation_answer(answer, _NEW, _OLD)


# 5
def test_a_members_bearer_is_refused_with_a_problem(scene: Scene) -> None:
    answer = renew(
        a_client_with_no_header_of_its_own(scene),
        _NAME,
        {"secret": _NEW},
        headers=a_bearer(scene, MEMBER),
    )

    assert_problem(answer, HTTPStatus.FORBIDDEN, _OLD, _NEW)


# 6
def test_a_members_bearer_beside_an_admin_cookie_carries_the_members_rights(
    scene: Scene,
) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = renew(
        browser,
        _NAME,
        {"secret": _NEW},
        headers={**a_bearer(scene, MEMBER), **SAME_ORIGIN},
    )

    assert_problem(answer, HTTPStatus.FORBIDDEN, _OLD, _NEW)


# 7
def test_an_invalid_bearer_beside_an_admin_cookie_is_refused(scene: Scene) -> None:
    browser = scene.browser()
    scene.sign_in(browser, groups=ADMIN_GROUPS)

    answer = renew(
        browser,
        _NAME,
        {"secret": _NEW},
        headers={"authorization": "Bearer nonsense", **SAME_ORIGIN},
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    assert 'error="invalid_token"' in answer.headers["www-authenticate"]
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)


# 8, 9
def test_an_admin_renews_a_token_and_the_store_holds_exactly_the_new_one(
    an_admin: Callable[[FakeSourceStore], Instance],
) -> None:
    instance = an_admin(an_https_store())

    answer = instance.renew(_NAME, {"secret": _NEW})

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert answer.content == b""
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)
    assert instance.store.secrets == {"the-source": _NEW}
    shown = whole_answers_of(instance, _NAME)
    assert _NEW not in shown
    assert _OLD not in shown


# 10, 11
@pytest.mark.parametrize(
    "secret", [None, "", "   ", f"first{chr(10)}second", f"a{chr(9)}b"]
)
def test_a_missing_blank_or_unprintable_token_is_a_422_problem_and_keeps_the_old_one(
    an_admin: Callable[[FakeSourceStore], Instance], secret: str | None
) -> None:
    instance = an_admin(an_https_store())

    answer = instance.renew(_NAME, {"secret": secret})

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, _OLD, _NEW)
    assert answer.json() == {
        "type": "about:blank",
        "title": "Unprocessable Content",
        "status": 422,
    }
    assert instance.store.secrets == {"the-source": _OLD}


# 12
def test_an_ssh_renewal_without_a_secret_shows_a_new_public_half_and_no_private_one(
    an_admin: Callable[[FakeSourceStore], Instance],
) -> None:
    instance = an_admin(a_source_store(a_source(_SSH)))
    before = instance.store.sources[0].public_key

    answer = instance.renew(_NAME, {"secret": None})

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert answer.content == b""
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)
    after = instance.client.get(
        PAGE.format(name=_NAME), headers={**instance.headers, **ASKING_FOR_JSON}
    ).json()["public_key"]
    assert after is not None
    assert after != before
    assert "PRIVATE" not in whole_answers_of(instance, _NAME)


# 13
def test_an_ssh_renewal_that_carries_a_token_renews_the_pair_and_stores_no_token(
    an_admin: Callable[[FakeSourceStore], Instance],
) -> None:
    instance = an_admin(a_source_store(a_source(_SSH)))

    answer = instance.renew(_NAME, {"secret": _NEW})

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert answer.content == b""
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)
    assert instance.store.sources[0].public_key is not None
    assert instance.store.secrets == {}
    assert _NEW not in whole_answers_of(instance, _NAME)


# 14
@pytest.mark.parametrize("secret", [_NEW, None])
def test_a_local_folder_cannot_be_renewed(
    an_admin: Callable[[FakeSourceStore], Instance], secret: str | None
) -> None:
    store = a_source_store(a_source(_LOCAL), secret=_OLD)
    instance = an_admin(store)

    answer = instance.renew(_NAME, {"secret": secret})

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, _OLD, _NEW)
    assert store.secrets == {"the-source": _OLD}
    assert store.sources[0].public_key is None


# 15
@pytest.mark.parametrize("name", ["no-such-source", "new"])
def test_an_unknown_source_is_a_404_problem_and_changes_nothing(
    an_admin: Callable[[FakeSourceStore], Instance], name: str
) -> None:
    instance = an_admin(an_https_store())

    answer = instance.renew(name, {"secret": _NEW})

    assert_problem(answer, HTTPStatus.NOT_FOUND, _OLD, _NEW)
    assert instance.store.secrets == {"the-source": _OLD}


# 16
class _SourceRemovedWhileRenewing(FakeSourceStore):
    def renew_deploy_key(self, source_id: str) -> bool:
        self.sources = []
        return super().renew_deploy_key(source_id)


def test_an_ssh_source_removed_while_renewing_is_a_404_problem() -> None:
    store = _SourceRemovedWhileRenewing(sources=[a_source(_SSH)])
    lobby = an_admin_over(decks_over(store))

    answer = renew(lobby.client, _NAME, {"secret": None})

    assert_problem(answer, HTTPStatus.NOT_FOUND, _OLD, _NEW)


# 17
def test_a_name_that_is_not_one_segment_is_the_frameworks_404() -> None:
    lobby = an_admin_over(decks_that_must_not_run())

    answer = renew(lobby.client, "a/b", {"secret": _NEW})

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"] != "application/problem+json"
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)


# 18
@pytest.mark.parametrize(
    "document",
    [
        {"secret": 12345},
        {"secret": ["a-listed-value"]},
        {"secret": {"a-key": "an-objects-value"}},
        {},
    ],
)
def test_a_malformed_body_is_the_validation_answer_without_what_was_sent(
    an_admin: Callable[[FakeSourceStore], Instance], document: dict[str, object]
) -> None:
    instance = an_admin(an_https_store())

    answer = instance.renew(_NAME, document)

    assert_validation_answer(
        answer, "12345", "a-listed-value", "a-key", "an-objects-value", _OLD, _NEW
    )
    assert instance.store.secrets == {"the-source": _OLD}


def test_a_body_that_is_not_json_is_the_validation_answer_without_what_was_sent() -> (
    None
):
    store = an_https_store()
    lobby = an_admin_over(decks_over(store))

    answer = lobby.client.post(
        ACCESS.format(name=_NAME),
        content=_NEW,
        headers={"content-type": "application/json"},
    )

    assert_validation_answer(answer, _NEW, _OLD)
    assert store.secrets == {"the-source": _OLD}


# 19, 20, 21
def test_a_same_site_write_with_the_own_origin_is_taken() -> None:
    store = an_https_store()
    lobby = an_admin_over(decks_over(store), headers={"accept": "text/html"})

    answer = renew(
        lobby.client,
        _NAME,
        {"secret": _NEW},
        headers={"sec-fetch-site": "same-site", "origin": "https://testserver"},
    )

    assert answer.status_code == HTTPStatus.NO_CONTENT
    assert_no_store(answer)
    assert store.secrets == {"the-source": _NEW}


@pytest.mark.parametrize(
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
def test_the_origin_check_refuses_this_json_write_before_any_port_is_touched(
    headers: dict[str, str],
) -> None:
    lobby = an_admin_over(decks_that_must_not_run(), headers={"accept": "text/html"})

    answer = renew(lobby.client, _NAME, {"secret": _NEW}, headers=headers)

    assert answer.status_code == HTTPStatus.FORBIDDEN
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)


# 22
@pytest.mark.parametrize("method", ["get", "put", "delete"])
def test_a_method_the_route_does_not_offer_is_the_frameworks_405(method: str) -> None:
    lobby = an_admin_over(decks_that_must_not_run())

    answer = getattr(lobby.client, method)(
        ACCESS.format(name=_NAME), headers=SAME_ORIGIN
    )

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert_no_store(answer)
    assert_nothing_returned(answer, _OLD, _NEW)


# 23
class _StoreThatFails(FakeSourceStore):
    def put_credential(self, source_id: str, secret: str) -> None:
        message = "the store is unavailable"
        raise RuntimeError(message)


def test_a_store_that_fails_is_the_plain_server_error_without_the_token() -> None:
    store = _StoreThatFails(sources=[a_source(_HTTPS)])
    quiet = without_raising(an_admin_over(decks_over(store)))

    answer = renew(quiet, _NAME, {"secret": _NEW})

    assert answer.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert answer.headers["content-type"] != "application/problem+json"
    assert_no_store(answer)
    assert _NEW not in answer.text


# 24
_A_NUMBER = 8675309
_TAB = chr(9)
_LINE_BREAK = chr(10)
_JSON = {"content-type": "application/json"}


@dataclass(frozen=True, slots=True)
class _Logged:
    """One request of rows 8-18 over fresh state, and the status the row expects."""

    store: Callable[[], FakeSourceStore]
    send: Callable[[Instance], Response]
    status: HTTPStatus


def _renewal(name: str, body: object) -> Callable[[Instance], Response]:
    return lambda instance: instance.renew(name, body)


def _reading(path: str) -> Callable[[Instance], Response]:
    return lambda instance: instance.client.get(
        path, headers={**instance.headers, **ASKING_FOR_JSON}
    )


def _not_json(instance: Instance) -> Response:
    return instance.client.post(
        ACCESS.format(name=_NAME),
        content=_NEW,
        headers={**instance.headers, **_JSON},
    )


def _ssh_store() -> FakeSourceStore:
    return a_source_store(a_source(_SSH))


def _local_store() -> FakeSourceStore:
    return a_source_store(a_source(_LOCAL), secret=_OLD)


def _removed_store() -> FakeSourceStore:
    return _SourceRemovedWhileRenewing(sources=[a_source(_SSH)])


_OK, _REFUSED, _GONE = (
    HTTPStatus.NO_CONTENT,
    HTTPStatus.UNPROCESSABLE_ENTITY,
    HTTPStatus.NOT_FOUND,
)
_LOGGED = {
    "8, 9 renewal": _Logged(an_https_store, _renewal(_NAME, {"secret": _NEW}), _OK),
    "8 source page": _Logged(
        an_https_store, _reading(PAGE.format(name=_NAME)), HTTPStatus.OK
    ),
    "8 source list": _Logged(an_https_store, _reading(LIST), HTTPStatus.OK),
    "10 null": _Logged(an_https_store, _renewal(_NAME, {"secret": None}), _REFUSED),
    "10 empty": _Logged(an_https_store, _renewal(_NAME, {"secret": ""}), _REFUSED),
    "10 blank": _Logged(an_https_store, _renewal(_NAME, {"secret": "   "}), _REFUSED),
    "11 line break": _Logged(
        an_https_store,
        _renewal(_NAME, {"secret": f"{_NEW}{_LINE_BREAK}{_OLD}"}),
        _REFUSED,
    ),
    "11 tab": _Logged(
        an_https_store, _renewal(_NAME, {"secret": f"{_NEW}{_TAB}{_OLD}"}), _REFUSED
    ),
    "12 ssh null": _Logged(_ssh_store, _renewal(_NAME, {"secret": None}), _OK),
    "13 ssh token": _Logged(_ssh_store, _renewal(_NAME, {"secret": _NEW}), _OK),
    "14 local token": _Logged(
        _local_store, _renewal(_NAME, {"secret": _NEW}), _REFUSED
    ),
    "14 local null": _Logged(_local_store, _renewal(_NAME, {"secret": None}), _REFUSED),
    "15 unknown": _Logged(
        an_https_store, _renewal("no-such-source", {"secret": _NEW}), _GONE
    ),
    "15 new": _Logged(an_https_store, _renewal("new", {"secret": _NEW}), _GONE),
    "16 removed": _Logged(_removed_store, _renewal(_NAME, {"secret": None}), _GONE),
    "17 two segments": _Logged(
        an_https_store, _renewal("a/b", {"secret": _NEW}), _GONE
    ),
    "18 number": _Logged(
        an_https_store, _renewal(_NAME, {"secret": _A_NUMBER}), _REFUSED
    ),
    "18 list": _Logged(an_https_store, _renewal(_NAME, {"secret": [_NEW]}), _REFUSED),
    "18 object": _Logged(
        an_https_store, _renewal(_NAME, {"secret": {_NEW: _NEW}}), _REFUSED
    ),
    "18 missing key": _Logged(
        an_https_store, _renewal(_NAME, {"other": _NEW}), _REFUSED
    ),
    "18 not JSON": _Logged(an_https_store, _not_json, _REFUSED),
}


@pytest.mark.parametrize("logged", _LOGGED.values(), ids=_LOGGED.keys())
def test_no_log_record_carries_a_token_or_a_malformed_value(
    an_admin: Callable[[FakeSourceStore], Instance],
    caplog: pytest.LogCaptureFixture,
    logged: _Logged,
) -> None:
    instance = an_admin(logged.store())

    with caplog.at_level(logging.DEBUG):
        answer = logged.send(instance)

    assert answer.status_code == logged.status
    assert caplog.records
    for value in (_NEW, _OLD, str(_A_NUMBER)):
        assert value not in caplog.text


# 25
def test_the_generated_spec_declares_the_renewal_with_its_problems_and_validation() -> (
    None
):
    spec = an_admin_over(decks_that_must_not_run()).app.openapi()
    responses = spec["paths"][ACCESS]["post"]["responses"]

    assert set(responses["422"]["content"]) == {PROBLEM_MEDIA_TYPE, "application/json"}
    for status in ("403", "404"):
        assert set(responses[status]["content"]) == {PROBLEM_MEDIA_TYPE}
    assert "204" in responses
    answer = responses["422"]["content"]["application/json"]["schema"]
    components = spec["components"]["schemas"]
    resolved = components[answer["$ref"].rsplit("/", 1)[-1]]
    assert resolved == validation_error_response_definition
    item = resolved["properties"]["detail"]["items"]["$ref"].rsplit("/", 1)[-1]
    assert components[item] == validation_error_definition
