"""An agent reads a person's deck view and deck text, and nothing of anyone else's.

Each scenario runs through the real lobby with the agents client's tokens: a
direct bearer GET on the route, the agent's tool call through `/mcp`, and the
person's own cookie GET as the answer both must equal.
"""

import json
import logging
from dataclasses import replace
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path

import pytest
from httpx2 import Response

from presentator.agent.mcp import AGENT_GROUP, MCP_GROUP
from presentator.api.deck_api import DECK_VIEW_PATH
from presentator.api.deck_text_api import DECK_TEXT_PATH
from presentator.contracts.copresenter import DeckText
from presentator.contracts.decks import (
    BuildAttempt,
    BuildFailure,
    BuildOutcome,
    DeckState,
)
from tests.presentator.agent.support.mcp_world import (
    World,
    a_world,
    called_through_mcp,
)
from tests.support.deck_text_support import A_DECK_TEXT, A_MIRROR_PATH, FakeDeckTexts
from tests.support.lobby import NOW
from tests.support.sign_in_support import MEMBER

_JSON = World.JSON
_NOT_FOUND = HTTPStatus.NOT_FOUND
_REFUSED = {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
_DECK_AND_TEXT = pytest.mark.parametrize(
    ("path", "tool"),
    [
        pytest.param(DECK_VIEW_PATH, "get_deck", id="deck"),
        pytest.param(DECK_TEXT_PATH, "get_deck_text", id="text"),
    ],
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return a_world(tmp_path)


def an_attempt(outcome: BuildOutcome, *, failure: BuildFailure | None) -> BuildAttempt:
    return BuildAttempt(
        commit="e91c5ad4f3b2a1908877665544332211aabbccdd",
        started_at=NOW - timedelta(minutes=3),
        outcome=outcome,
        failure=failure,
    )


def the_deck_has(world: World, *, attempt: BuildAttempt | None, built: bool) -> None:
    """Felix's deck as the store holds it after a build ran, or did not."""
    deck = world.store.kept[World.FELIX_DECK]
    build = deck.build if built else None
    world.store.kept[World.FELIX_DECK] = replace(deck, attempt=attempt, build=build)


def felix_by_bearer(world: World, path: str) -> Response:
    return world.program().get(path, headers={**world.bearer(), **_JSON})


def felix_by_cookie(world: World, path: str) -> Response:
    return world.felix.get(path, headers=_JSON)


@_DECK_AND_TEXT
def test_an_agent_reads_a_built_deck_as_the_person_does_through_both_entry_points(
    world: World, path: str, tool: str
) -> None:
    address = path.format(slug=World.FELIX_DECK)

    by_cookie = felix_by_cookie(world, address)
    by_bearer = felix_by_bearer(world, address)
    by_tool = called_through_mcp(
        world.program(), world.bearer(), tool, {"slug": World.FELIX_DECK}
    )

    assert by_cookie.status_code == HTTPStatus.OK
    assert by_bearer.json() == by_cookie.json()
    assert not by_tool.is_error
    assert json.loads(by_tool.text) == by_cookie.json()


def test_the_text_holds_each_slides_number_title_body_and_notes(world: World) -> None:
    answer = felix_by_bearer(world, DECK_TEXT_PATH.format(slug=World.FELIX_DECK))

    assert answer.json()["slides"] == [
        {
            "number": slide.number,
            "title": slide.title,
            "body": slide.body,
            "notes": slide.notes,
        }
        for slide in A_DECK_TEXT.slides
    ]


@pytest.mark.parametrize(
    ("attempt", "state"),
    [
        pytest.param(None, DeckState.NEVER_BUILT, id="before the first build"),
        pytest.param(
            an_attempt(BuildOutcome.RUNNING, failure=None),
            DeckState.BUILDING,
            id="while building",
        ),
        pytest.param(
            an_attempt(BuildOutcome.FAILED, failure=BuildFailure.said("boom at /x")),
            DeckState.FAILED,
            id="after a failed first build",
        ),
    ],
)
def test_a_deck_with_no_build_shows_its_state_and_has_no_text(
    world: World, attempt: BuildAttempt | None, state: DeckState
) -> None:
    the_deck_has(world, attempt=attempt, built=False)
    view_address = DECK_VIEW_PATH.format(slug=World.FELIX_DECK)
    text_address = DECK_TEXT_PATH.format(slug=World.FELIX_DECK)

    view = called_through_mcp(
        world.program(), world.bearer(), "get_deck", {"slug": World.FELIX_DECK}
    )
    text = called_through_mcp(
        world.program(), world.bearer(), "get_deck_text", {"slug": World.FELIX_DECK}
    )
    view_by_cookie = felix_by_cookie(world, view_address)
    text_by_cookie = felix_by_cookie(world, text_address)

    assert not view.is_error
    assert json.loads(view.text) == view_by_cookie.json()
    assert felix_by_bearer(world, view_address).json() == view_by_cookie.json()
    assert json.loads(view.text)["state"] == state
    assert text_by_cookie.status_code == _NOT_FOUND
    assert felix_by_bearer(world, text_address).content == text_by_cookie.content
    assert text.is_error
    assert text.text == text_by_cookie.text


def test_after_a_failed_rebuild_the_view_shows_the_failure_and_the_text_the_last_build(
    tmp_path: Path,
) -> None:
    failed = an_attempt(BuildOutcome.FAILED, failure=BuildFailure.said("it broke"))
    texts = FakeDeckTexts(
        by_commit={failed.commit: replace(A_DECK_TEXT, title="Failed commit")}
    )
    world = a_world(tmp_path, texts=texts)
    the_deck_has(world, attempt=failed, built=True)

    view = called_through_mcp(
        world.program(), world.bearer(), "get_deck", {"slug": World.FELIX_DECK}
    )
    text = called_through_mcp(
        world.program(), world.bearer(), "get_deck_text", {"slug": World.FELIX_DECK}
    )

    shown = json.loads(view.text)
    assert shown["state"] == DeckState.FAILED
    assert shown["attempt"]["failure"]["text"] == "it broke"
    assert not text.is_error
    assert json.loads(text.text)["title"] == A_DECK_TEXT.title


@pytest.mark.parametrize(
    ("text", "answers"),
    [
        pytest.param(None, _NOT_FOUND, id="a built commit without slides.md"),
        pytest.param(
            DeckText(title="Empty", language=None, slides=()),
            HTTPStatus.OK,
            id="a slides.md with no slide",
        ),
    ],
)
def test_a_deck_without_slides_is_not_found_and_one_with_none_is_an_empty_list(
    tmp_path: Path, text: DeckText | None, answers: HTTPStatus
) -> None:
    world = a_world(tmp_path, texts=FakeDeckTexts(text=text))
    address = DECK_TEXT_PATH.format(slug=World.FELIX_DECK)

    by_cookie = felix_by_cookie(world, address)
    by_bearer = felix_by_bearer(world, address)
    by_tool = called_through_mcp(
        world.program(), world.bearer(), "get_deck_text", {"slug": World.FELIX_DECK}
    )

    assert by_cookie.status_code == answers
    assert by_bearer.status_code == answers
    assert by_bearer.content == by_cookie.content
    if text is None:
        assert by_tool.is_error
        assert by_tool.text.encode() == by_cookie.content
    else:
        assert not by_tool.is_error
        assert json.loads(by_tool.text) == by_cookie.json()
        assert by_cookie.json()["slides"] == []


@_DECK_AND_TEXT
@pytest.mark.parametrize("slug", [World.ANNA_DECK, "no-such-deck"])
def test_another_persons_deck_and_an_unknown_one_give_the_same_not_found_bytes(
    world: World, path: str, tool: str, slug: str
) -> None:
    address = path.format(slug=slug)

    by_cookie = felix_by_cookie(world, address)
    by_bearer = felix_by_bearer(world, address)
    by_tool = called_through_mcp(world.program(), world.bearer(), tool, {"slug": slug})

    assert by_cookie.status_code == _NOT_FOUND
    assert by_bearer.status_code == _NOT_FOUND
    assert by_bearer.content == by_cookie.content
    assert by_tool.is_error
    assert by_tool.text.encode() == by_cookie.content


def test_an_unreadable_mirror_is_a_503_problem_and_no_usable_answer_to_the_agent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    world = a_world(tmp_path, texts=FakeDeckTexts(unreadable=True))
    address = DECK_TEXT_PATH.format(slug=World.FELIX_DECK)

    with caplog.at_level(logging.DEBUG):
        by_cookie = felix_by_cookie(world, address)
        by_bearer = felix_by_bearer(world, address)
        by_tool = called_through_mcp(
            world.program(), world.bearer(), "get_deck_text", {"slug": World.FELIX_DECK}
        )

    assert by_cookie.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert by_bearer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert by_bearer.headers["content-type"].startswith("application/problem+json")
    assert by_tool.is_error
    assert "usable answer" in by_tool.text
    for leaked in (A_DECK_TEXT.slides[0].body, A_DECK_TEXT.title, A_MIRROR_PATH):
        assert leaked not in by_tool.text
        assert leaked not in caplog.text


@_DECK_AND_TEXT
@pytest.mark.parametrize(
    "value",
    ["", ".", "..", "felix-deck/x", "a\\b", 'a"b', "a\x00b", "a\nb"],
    ids=["empty", "dot", "dotdot", "slash", "backslash", "quote", "nul", "newline"],
)
def test_a_literal_path_value_is_refused_or_not_found_and_never_another_deck(
    world: World, path: str, tool: str, value: str
) -> None:
    del path

    answer = called_through_mcp(world.program(), world.bearer(), tool, {"slug": value})

    assert answer.is_error
    assert World.FELIX_DECK not in answer.text
    assert World.ANNA_DECK not in answer.text


@_DECK_AND_TEXT
@pytest.mark.parametrize(
    "value",
    ["%2F", "%2E%2E", "%5C", "%00", "%0A", f"{World.FELIX_DECK}%2F%2E%2E"],
    ids=["slash", "dotdot", "backslash", "nul", "newline", "deck-then-dotdot"],
)
def test_an_encoded_path_value_is_refused_or_not_found_and_never_another_deck(
    world: World, path: str, tool: str, value: str
) -> None:
    del tool
    address = path.format(slug=value)

    answer = world.program().get(address, headers={**world.bearer(), **_JSON})

    assert answer.status_code != HTTPStatus.OK
    assert answer.status_code < HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@_DECK_AND_TEXT
def test_the_resource_bearer_refuses_every_write_method_on_the_marked_deck_paths(
    world: World, path: str, tool: str, method: str
) -> None:
    del tool
    address = path.format(slug=World.FELIX_DECK)

    answer = world.program().request(
        method, address, headers={**world.bearer(), **_JSON}
    )

    assert answer.status_code in _REFUSED
    assert world.store.get(World.FELIX_DECK) is not None


@_DECK_AND_TEXT
def test_cookie_only_the_owner_reads_the_deck_and_another_person_gets_not_found(
    world: World, path: str, tool: str
) -> None:
    del tool
    address = path.format(slug=World.FELIX_DECK)

    owner = world.felix.get(address, headers=_JSON)
    other = world.anna.get(address, headers=_JSON)

    assert owner.status_code == HTTPStatus.OK
    assert other.status_code == _NOT_FOUND


@_DECK_AND_TEXT
def test_an_own_account_agent_finds_no_deck_of_a_person(
    world: World, path: str, tool: str
) -> None:
    headers = world.own_agent(groups=[*MEMBER, MCP_GROUP, AGENT_GROUP])

    by_tool = called_through_mcp(
        world.program(), headers, tool, {"slug": World.FELIX_DECK}
    )
    by_bearer = world.program().get(
        path.format(slug=World.FELIX_DECK), headers={**headers, **_JSON}
    )

    assert by_tool.is_error
    assert json.loads(by_tool.text)["status"] == _NOT_FOUND
    assert by_bearer.status_code == _NOT_FOUND


@_DECK_AND_TEXT
def test_the_cookie_and_a_configured_person_bearer_read_beside_the_resource(
    tmp_path: Path, path: str, tool: str
) -> None:
    del tool
    world = a_world(
        tmp_path,
        token_clients=frozenset({World.HAL}),
    )
    address = path.format(slug=World.FELIX_DECK)
    token = world.bearer(authorized_party=World.HAL, audience=None, groups=MEMBER)

    by_cookie = world.felix.get(address, headers=_JSON)
    by_person_bearer = world.program().get(address, headers={**token, **_JSON})

    assert by_cookie.status_code == HTTPStatus.OK
    assert by_person_bearer.json() == by_cookie.json()
