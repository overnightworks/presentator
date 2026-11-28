"""An admin names the member who owns a new source; nobody else is named."""

from dataclasses import replace
from http import HTTPStatus
from typing import Final

import pytest
from webauth.ports import AccountKind

from presentator.application.decks import SourceRefusal
from tests.presentator.api.support.source_api_support import (
    A_SOURCE_NAME,
    CHECK,
    LIST,
    PAGE,
    SENT,
    World,
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
from tests.support.lobby import ADMIN, NEIGHBOUR, USERNAME
from tests.support.lobby_reads import listed_titles

_MEMBER_ID: Final = f"id-of-{NEIGHBOUR}"
_AN_AGENT: Final = "a-gateway-agent"
_SSH: Final = "git@git.example.invalid:talks.git"
_THE_ONLY_DECK: Final = "A talk"
_FIRST_ADDED_SOURCE: Final = "added-1"
_DIRECTORY: Final = FakePersonDirectory(
    accounts=(
        FakeAccount(id=_MEMBER_ID, username=NEIGHBOUR, role="user"),
        FakeAccount(
            id="id-of-the-agent",
            username=_AN_AGENT,
            role="user",
            kind=AccountKind.AGENT,
        ),
    )
)


def a_world_of_members() -> World:
    """The admin's world, in which `NEIGHBOUR` is a member and one agent exists."""
    world = a_world()
    folders = FakeDeckFolders(carried={_FIRST_ADDED_SOURCE: (a_folder("hello"),)})
    return replace(
        world, decks=replace(world.decks, members=_DIRECTORY, folders=folders)
    )


def a_checked_body(**changes: object) -> dict[str, object]:
    return checked_body(name=A_SOURCE_NAME, **changes)


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
    body = a_checked_body(owner=typed)
    body["fingerprint"] = client.post(CHECK, json=body).json()["fingerprint"]

    answer = client.post(LIST, json=body)

    assert answer.status_code == HTTPStatus.CREATED
    assert [source.owner_id for source in world.store.sources] == [owner_id]


@pytest.mark.parametrize(
    "typed",
    ["nobody", _AN_AGENT, NEIGHBOUR.title(), "ann"],
    ids=["unknown", "an agent", "another case", "a prefix"],
)
def test_an_owner_who_is_no_member_is_refused_and_nothing_is_written(
    typed: str,
) -> None:
    world = a_world_of_members()
    client = an_admin_over(world.decks).client
    body = a_checked_body(owner=typed)
    body["fingerprint"] = client.post(CHECK, json=body).json()["fingerprint"]

    answer = client.post(LIST, json=body)

    assert_problem(answer, HTTPStatus.UNPROCESSABLE_ENTITY, SENT, "webhook_secret")
    assert answer.json()["reason"] == SourceRefusal.UNKNOWN_OWNER
    assert world.store.sources == []
    assert world.store.hashes == {}


def test_a_corrected_owner_creates_an_ssh_source_on_the_same_check_and_key() -> None:
    world = a_world_of_members()
    client = an_admin_over(world.decks).client
    drafted = draft(client).json()
    body = a_checked_body(url=_SSH, secret=None, key_draft_id=drafted["id"])
    body["fingerprint"] = client.post(CHECK, json=body).json()["fingerprint"]

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
    body = a_checked_body(owner=NEIGHBOUR)
    body["fingerprint"] = admin.client.post(CHECK, json=body).json()["fingerprint"]
    admin.client.post(LIST, json=body)

    source_page = admin.client.get(
        PAGE.format(name=A_SOURCE_NAME), headers={"accept": "application/json"}
    ).json()

    assert listed_titles(member.client) == [_THE_ONLY_DECK]
    assert listed_titles(admin.client) == []
    assert source_page["decks"] == []
