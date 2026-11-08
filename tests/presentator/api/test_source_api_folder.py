"""A source's folder through the JSON API: create, refuse, one address, one proof."""

from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.application.decks import SourceRefusal
from tests.presentator.api.support.source_api_support import (
    A_SOURCE_NAME,
    CHECK,
    LIST,
    SENT,
    a_world,
    an_admin_over,
    assert_problem,
    assert_validation_answer,
    checked_body,
)

_ANOTHER_NAME = "another"


def create(client: TestClient, **changes: object) -> Response:
    """Check the values without a folder, then create with the rest of them."""
    body = checked_body(**{"name": A_SOURCE_NAME} | changes)
    probe = {key: value for key, value in body.items() if key != "folder"}
    body["fingerprint"] = client.post(CHECK, json=probe).json()["fingerprint"]
    return client.post(LIST, json=body)


def test_a_check_gives_one_proof_whether_or_not_the_request_names_a_folder() -> None:
    client = an_admin_over(a_world().decks).client

    plain = client.post(CHECK, json=checked_body()).json()
    with_folder = client.post(CHECK, json=checked_body(folder="examples")).json()

    assert plain["fingerprint"] == with_folder["fingerprint"]


@pytest.mark.parametrize(
    ("changes", "stored_folder"),
    [({"folder": "examples"}, "examples"), ({}, "")],
    ids=["a folder", "no folder"],
)
def test_a_create_stores_the_folder_it_names_or_the_top_of_the_repository(
    changes: dict[str, object], stored_folder: str
) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    answer = create(client, **changes)

    assert answer.status_code == HTTPStatus.CREATED
    assert [source.folder for source in world.store.sources] == [stored_folder]


def test_a_null_folder_is_the_validation_answer() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    answer = create(client, folder=None)

    assert_validation_answer(answer, SENT)
    assert world.store.sources == []


# SR8
def test_a_malformed_folder_is_refused_with_its_reason_and_stores_nothing() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client

    answer = create(client, folder="../elsewhere")

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.MALFORMED_FOLDER
    assert world.store.sources == []


# SR8
def test_another_folder_on_the_same_address_is_a_duplicate_url() -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    create(client, folder="examples")

    answer = create(client, name=_ANOTHER_NAME, folder="talks")

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.DUPLICATE_URL
    assert len(world.store.sources) == 1


@pytest.mark.parametrize("folder", ["", "examples"])
def test_the_source_page_names_the_stored_folder(folder: str) -> None:
    world = a_world()
    client = an_admin_over(world.decks).client
    create(client, folder=folder)

    shown = client.get(
        f"{LIST}/{A_SOURCE_NAME}", headers={"accept": "application/json"}
    ).json()

    assert shown["folder"] == folder
