"""Every host's validation answer keeps its shape and repeats nothing sent."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import cast

import pytest
from fastapi.openapi.utils import (
    validation_error_definition,
    validation_error_response_definition,
)
from httpx2 import Response
from sqlalchemy import Engine

from presentator.api.json_bodies import UNSTORABLE_STRING
from presentator.contracts.decks import AccessKind
from presentator.contracts.preferences import ThemeChoice
from tests.support.copresenter_support import ASK, two_hosts_with_a_copresenter
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_TALK_ADDRESS,
    NEIGHBOUR,
    Lobby,
    a_lobby,
)
from tests.support.log_lines import request_lines
from tests.support.sign_in_support import ADMIN, Scene, a_scene

_ADMIN_PEOPLE = "/api/admin/people/"
_HAL = "hal-client"
_MARKER = "zq-marker-7431"
_NUMBER_MARKER = 987654321
_ACCOUNT = "/api/account"
_SETTINGS = "/api/settings"
_KIND = "/api/sources/new/kind"
_JSON = {"accept": "application/json"}
_NO_STORE = "no-store"


def a_member_lobby() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in(username=NEIGHBOUR)
    return lobby


def an_admin_lobby() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in()
    return lobby


def the_error_keys() -> tuple[set[str], set[str]]:
    """The keys the published `ValidationError` requires, and all it may carry."""
    return (
        set(validation_error_definition["required"]),
        set(validation_error_definition["properties"]),
    )


def is_the_frameworks_validation_answer(answer: Response, *, marker: object) -> None:
    assert answer.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert answer.headers["content-type"] == "application/json"
    assert answer.headers["cache-control"] == _NO_STORE
    assert set(answer.json()) == {"detail"}
    required, allowed = the_error_keys()
    errors = answer.json()["detail"]
    assert errors
    for error in errors:
        assert set(error) == required
        assert set(error) <= allowed
    assert str(marker) not in answer.text


def test_a_theme_outside_the_set_is_answered_without_the_value_and_stores_nothing() -> (
    None
):
    lobby = a_member_lobby()

    answer = lobby.client.put(_ACCOUNT, json={"theme": _MARKER}, headers=_JSON)

    is_the_frameworks_validation_answer(answer, marker=_MARKER)
    assert lobby.client.get(_ACCOUNT, headers=_JSON).json()["theme"] == (
        ThemeChoice.FOLLOW_SYSTEM.value
    )


def test_a_name_of_the_wrong_json_type_is_answered_without_the_value() -> None:
    lobby = an_admin_lobby()

    answer = lobby.client.put(
        _SETTINGS,
        json={"name": _NUMBER_MARKER},
        headers=_JSON,
    )

    is_the_frameworks_validation_answer(answer, marker=_NUMBER_MARKER)


def test_a_body_that_is_not_json_is_answered_without_a_word_of_it() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(
        _ACCOUNT,
        content=f'{{"theme": "{_MARKER}"'.encode(),
        headers={**_JSON, "content-type": "application/json"},
    )

    is_the_frameworks_validation_answer(answer, marker=_MARKER)


def test_an_admin_route_answers_a_bad_query_without_the_value(
    engine: Engine,
) -> None:
    scene: Scene = a_scene(engine, token_clients=frozenset({_HAL}))
    minted = scene.issuer.mint_access_token(
        subject="a-person", groups=ADMIN, authorized_party=_HAL, audience=None
    )

    answer = scene.lobby.client.delete(
        f"{_ADMIN_PEOPLE}someone?dry_run={_MARKER}",
        headers={"authorization": f"Bearer {minted}", **_JSON},
    )

    is_the_frameworks_validation_answer(answer, marker=_MARKER)


def test_the_generated_spec_keeps_the_frameworks_validation_definitions() -> None:
    schemas = a_lobby().app.openapi()["components"]["schemas"]

    assert schemas["HTTPValidationError"] == validation_error_response_definition
    assert schemas["ValidationError"] == validation_error_definition


def _references(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        members = cast("dict[str, object]", node)
        reference = members.get("$ref")
        if isinstance(reference, str):
            yield reference
        for value in members.values():
            yield from _references(value)
    elif isinstance(node, list):
        for value in cast("list[object]", node):
            yield from _references(value)


def test_every_reference_of_the_generated_spec_resolves() -> None:
    spec = a_lobby().app.openapi()

    for reference in _references(spec):
        target: object = spec
        for part in reference.removeprefix("#/").split("/"):
            target = cast("dict[str, object]", target)[part]


@dataclass(frozen=True, slots=True)
class JsonSent:
    """A JSON body one browser sends to one route."""

    address: str
    method: str
    path: str
    body: bytes


_LONE_SURROGATE = "\\ud"
_ESCAPED_NUL = "\\u0000"


def _sent_by_the_admin(engine: Engine, sent: JsonSent) -> Response:
    hosts, _ = two_hosts_with_a_copresenter(engine)
    browser = hosts.signed_in_at(sent.address, headers=A_BROWSERS_HEADERS)
    return browser.request(
        sent.method,
        sent.path,
        content=sent.body,
        headers={**_JSON, "content-type": "application/json"},
    )


@pytest.mark.parametrize(
    ("sent", "marker"),
    [
        pytest.param(
            JsonSent(
                A_TALK_ADDRESS,
                "POST",
                ASK,
                rb'{"said": "Was ist \ud800 hier?", "slide": 1}',
            ),
            _LONE_SURROGATE,
            id="ask-surrogate-value",
        ),
        pytest.param(
            JsonSent(A_TALK_ADDRESS, "POST", ASK, rb'{"said": "Hier?", "\udfff": 1}'),
            _LONE_SURROGATE,
            id="ask-surrogate-key",
        ),
        pytest.param(
            JsonSent(
                A_TALK_ADDRESS, "POST", ASK, rb'{"said": "Hier\u0000?", "slide": 1}'
            ),
            _ESCAPED_NUL,
            id="ask-nul",
        ),
        pytest.param(
            JsonSent(A_BROWSERS_ADDRESS, "PUT", _ACCOUNT, rb'{"theme": "\ud800"}'),
            _LONE_SURROGATE,
            id="account-surrogate",
        ),
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS, "PUT", _SETTINGS, rb'{"name": "Vor\udc00trag"}'
            ),
            _LONE_SURROGATE,
            id="settings-surrogate",
        ),
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS, "PUT", _SETTINGS, rb'{"name": "Vor\u0000trag"}'
            ),
            _ESCAPED_NUL,
            id="settings-nul",
        ),
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS,
                "POST",
                _KIND,
                rb'{"url": "https://a.example/\ud800"}',
            ),
            _LONE_SURROGATE,
            id="kind-surrogate",
        ),
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS,
                "POST",
                _KIND,
                rb'{"url": "https://a.example/", "also": ["x\u0000"]}',
            ),
            _ESCAPED_NUL,
            id="kind-nul-in-an-ignored-list",
        ),
    ],
)
def test_a_string_no_route_can_store_is_answered_422_without_the_input(
    sent: JsonSent,
    marker: str,
    engine: Engine,
    json_logging: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del json_logging
    caplog.set_level(logging.INFO)

    answer = _sent_by_the_admin(engine, sent)

    is_the_frameworks_validation_answer(answer, marker=marker)
    assert {error["type"] for error in answer.json()["detail"]} == {UNSTORABLE_STRING}
    (line,) = request_lines(caplog)
    assert line["status"] == HTTPStatus.UNPROCESSABLE_ENTITY
    assert line["route"] == sent.path


@pytest.mark.parametrize(
    "sent",
    [
        JsonSent(
            A_TALK_ADDRESS,
            "POST",
            ASK,
            b'{"said": "' + _MARKER.encode() + b'\xff", "slide": 1}',
        ),
        JsonSent(
            A_BROWSERS_ADDRESS,
            "PUT",
            _SETTINGS,
            b'{"name": "' + _MARKER.encode() + b'\xed\xa0\x80"}',
        ),
        JsonSent(
            A_BROWSERS_ADDRESS,
            "POST",
            _KIND,
            b'{"url": "' + _MARKER.encode() + b'\xc3"}',
        ),
    ],
    ids=["ask-stray-byte", "settings-encoded-surrogate", "kind-cut-sequence"],
)
def test_a_body_that_is_not_utf8_is_answered_as_invalid_json(
    sent: JsonSent,
    engine: Engine,
    json_logging: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    del json_logging
    caplog.set_level(logging.INFO)

    answer = _sent_by_the_admin(engine, sent)

    is_the_frameworks_validation_answer(answer, marker=_MARKER)
    assert {error["type"] for error in answer.json()["detail"]} == {"json_invalid"}
    (line,) = request_lines(caplog)
    assert line["route"] == sent.path


_BEYOND_PYTHONS_DIGIT_LIMIT = b"1" * 5000


@pytest.mark.parametrize(
    "sent",
    [
        JsonSent(
            A_TALK_ADDRESS,
            "POST",
            ASK,
            b'{"said": "Hier?", "slide": ' + _BEYOND_PYTHONS_DIGIT_LIMIT + b"}",
        ),
        JsonSent(
            A_BROWSERS_ADDRESS,
            "PUT",
            _SETTINGS,
            b'{"name": ' + _BEYOND_PYTHONS_DIGIT_LIMIT + b"}",
        ),
    ],
    ids=["ask", "settings"],
)
def test_an_integer_too_long_to_read_is_a_client_error(
    sent: JsonSent, engine: Engine
) -> None:
    answer = _sent_by_the_admin(engine, sent)

    assert HTTPStatus(answer.status_code).is_client_error


@pytest.mark.parametrize(
    ("sent", "read_back"),
    [
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS,
                "PUT",
                _SETTINGS,
                rb'{"name": "Vortrag \ud83d\ude00"}',
            ),
            {"name": "Vortrag \N{GRINNING FACE}"},
            id="settings",
        ),
        pytest.param(
            JsonSent(
                A_BROWSERS_ADDRESS,
                "POST",
                _KIND,
                rb'{"url": "https://a.example/\ud83d\ude00"}',
            ),
            {"access": AccessKind.HTTPS.value},
            id="kind",
        ),
    ],
)
def test_a_surrogate_pair_is_one_character_every_route_takes(
    sent: JsonSent, read_back: dict[str, str], engine: Engine
) -> None:
    answer = _sent_by_the_admin(engine, sent)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json() == read_back
