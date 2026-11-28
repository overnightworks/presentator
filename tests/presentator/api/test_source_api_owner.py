"""An admin names the member who owns a new source; nobody else is named."""

from dataclasses import replace
from http import HTTPStatus
from typing import Final

import pytest
from fastapi.testclient import TestClient

from presentator.application.decks import SourceRefusal
from tests.presentator.api.support.source_api_support import (
    A_SOURCE_NAME,
    CHECK,
    LIST,
    PAGE,
    SENT,
    World,
    a_created_source,
    a_world,
    an_admin_over,
    assert_problem,
    checked_body,
    draft,
)
from tests.presentator.application.support.decks_support import a_folder
from tests.presentator.application.support.fakes import (
    FakeAccount,
    FakeDeckFolders,
    FakePersonDirectory,
)
from tests.support.lobby import ADMIN, NEIGHBOUR, USERNAME, account_id_of
from tests.support.lobby_reads import listed_titles

_MEMBER_ID: Final = account_id_of(NEIGHBOUR)
_SSH: Final = "git@git.example.invalid:talks.git"
_THE_ONLY_DECK: Final = "A talk"
_FIRST_ADDED_SOURCE: Final = "added-1"
_DIRECTORY: Final = FakePersonDirectory(
    accounts=(FakeAccount(id=_MEMBER_ID, username=NEIGHBOUR, role="user"),)
)


def a_world_of_members() -> World:
    """The admin's world, in which `NEIGHBOUR` is a member."""
    world = a_world()
    folders = FakeDeckFolders(carried={_FIRST_ADDED_SOURCE: (a_folder("hello"),)})
    return replace(
        world, decks=replace(world.decks, members=_DIRECTORY, folders=folders)
    )


def a_passed_check(client: TestClient, **changes: object) -> dict[str, object]:
    """The body of a source whose Check this client has passed."""
    body = checked_body(name=A_SOURCE_NAME, **changes)
    body["fingerprint"] = client.post(CHECK, json=body).json()["fingerprint"]
    return body


@pytest.mark.parametrize(
    ("typed", "owner_id"),
    [
        (NEIGHBOUR, _MEMBER_ID),
        (f"  {NEIGHBOUR}  ", _MEMBER_ID),
        ("", ADMIN),
        ("   ", ADMIN),
        (USERNAME, ADMIN),
    ],
    ids=[
        "a member",
        "a member with spaces",
        "empty",
        "only spaces",
        "the admin itself",
    ],
)
def test_the_owner_field_names_the_member_or_leaves_the_admin_the_owner(
    typed: str, owner_id: str
) -> None:
    world = a_world_of_members()
    client = an_admin_over(world.decks).client

    a_created_source(client, {"owner": typed})

    assert [source.owner_id for source in world.store.sources] == [owner_id]


def test_an_owner_who_is_no_member_is_refused_and_nothing_is_written() -> None:
    world = a_world_of_members()
    client = an_admin_over(world.decks).client
    body = a_passed_check(client)

    answer = client.post(LIST, json=body | {"owner": "nobody"})

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.UNKNOWN_OWNER
    assert world.store.sources == []
    assert world.store.hashes == {}


def test_a_corrected_owner_creates_an_ssh_source_on_the_same_check_and_key() -> None:
    world = a_world_of_members()
    client = an_admin_over(world.decks).client
    drafted = draft(client).json()
    body = a_passed_check(client, url=_SSH, secret=None, key_draft_id=drafted["id"])

    refused = client.post(LIST, json=body | {"owner": "nobody"})
    created = client.post(LIST, json=body | {"owner": NEIGHBOUR})

    assert refused.json()["reason"] == SourceRefusal.UNKNOWN_OWNER
    assert created.status_code == HTTPStatus.CREATED
    (stored,) = world.store.sources
    assert (stored.owner_id, stored.public_key) == (_MEMBER_ID, drafted["public_key"])


def test_only_the_member_sees_the_decks_of_a_source_the_admin_made_for_them() -> None:
    world = a_world_of_members()
    admin = an_admin_over(world.decks)
    member = admin.another_browser()
    member.sign_in(username=NEIGHBOUR)
    a_created_source(admin.client, {"owner": NEIGHBOUR})

    source_page = admin.client.get(
        PAGE.format(name=A_SOURCE_NAME), headers={"accept": "application/json"}
    ).json()

    assert listed_titles(member.client) == [_THE_ONLY_DECK]
    assert listed_titles(admin.client) == []
    assert source_page["decks"] == []
