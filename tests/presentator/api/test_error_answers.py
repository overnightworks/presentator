"""A lobby route's validation answer keeps its shape and repeats nothing sent."""

from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import cast

from fastapi.openapi.utils import (
    validation_error_definition,
    validation_error_response_definition,
)
from httpx2 import Response

from presentator.api.preferences import THEME
from tests.support.lobby import NEIGHBOUR, Lobby, a_lobby
from tests.support.sign_in_support import Scene, a_scene

_ADMIN_PEOPLE = "/api/admin/people/"
_HAL = "hal-client"
_ADMIN_GROUPS = ["/apps/presentator", "/apps/presentator/admin"]
_MARKER = "zq-marker-7431"
_NUMBER_MARKER = 987654321
_ACCOUNT = "/api/account"
_JSON = {"accept": "application/json"}
_NO_STORE = "no-store"


def a_member_lobby() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in(username=NEIGHBOUR)
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

    answer = lobby.client.put(
        _ACCOUNT, json={"language_tag": None, "theme": _MARKER}, headers=_JSON
    )

    is_the_frameworks_validation_answer(answer, marker=_MARKER)
    assert lobby.client.get(_ACCOUNT, headers=_JSON).json()["theme"] is None


def test_a_language_of_the_wrong_json_type_is_answered_without_the_value() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.put(
        _ACCOUNT, json={"language_tag": _NUMBER_MARKER, "theme": None}, headers=_JSON
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


def test_a_form_post_with_a_theme_outside_the_set_is_answered_without_the_value() -> (
    None
):
    lobby = a_member_lobby()

    answer = lobby.client.post(THEME, data={"theme": _MARKER})

    is_the_frameworks_validation_answer(answer, marker=_MARKER)


def test_an_admin_route_answers_a_bad_query_without_the_value_and_deletes_nothing(
    tmp_path: Path,
) -> None:
    scene: Scene = a_scene(tmp_path, token_clients=frozenset({_HAL}))
    minted = scene.issuer.mint_access_token(
        subject="a-person", groups=_ADMIN_GROUPS, authorized_party=_HAL, audience=None
    )
    people_before = scene.count("users")

    answer = scene.lobby.client.delete(
        f"{_ADMIN_PEOPLE}someone?dry_run={_MARKER}",
        headers={"authorization": f"Bearer {minted}", **_JSON},
    )

    is_the_frameworks_validation_answer(answer, marker=_MARKER)
    assert scene.count("users") == people_before


def test_a_problem_stays_a_status_only_problem() -> None:
    lobby = a_member_lobby()

    answer = lobby.client.get("/api/decks/no-such-deck", headers=_JSON)

    assert answer.status_code == HTTPStatus.NOT_FOUND
    assert answer.headers["content-type"] == "application/problem+json"
    assert answer.json() == {
        "type": "about:blank",
        "title": HTTPStatus.NOT_FOUND.phrase,
        "status": HTTPStatus.NOT_FOUND.value,
    }


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
