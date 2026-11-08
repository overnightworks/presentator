"""The signed-in world the agent tests drive: two people, each with one built deck.

The lobby runs over the real SQLite identity stores and the fake issuer; two
people sign in through the real callback, then a program that is no browser
calls with a bearer alone.
"""

import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent_runtime_client.mcp_wire import (
    MCP_ENDPOINT_PATH,
    PROTOCOL_VERSION,
    PROTOCOL_VERSION_HEADER,
)
from fastapi.testclient import TestClient
from httpx2 import Response
from mcp.shared.inbound import MCP_METHOD_HEADER, MCP_NAME_HEADER
from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

from presentator.agent.mcp import MCP_CLIENT_ID, MCP_GROUP, MCP_SCOPE
from presentator.api.deck_api import DECK_VIEW_PATH
from presentator.api.deck_list_api import DECK_LIST_PATH
from presentator.api.deck_text_api import DECK_TEXT_PATH
from presentator.ports.copresenter import DeckTexts
from tests.presentator.application.support.fakes import FakeDeckStore, FrozenClock
from tests.support.deck_text_support import FakeDeckTexts
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    NOW,
    GivenDecks,
    a_configured_source,
    a_decks,
    put_a_built_deck,
)
from tests.support.sign_in_support import MEMBER, Scene, a_scene


class World:
    """A scene whose two people each own one deck, and the deck store behind it."""

    HAL = "hal-client"
    SIGNED_IN = 2
    FELIX = "subject-of-felix"
    ANNA = "subject-of-anna"
    STRANGER = "subject-of-nobody-who-signed-in"
    MCP_URL = f"{A_BROWSERS_ADDRESS}{MCP_ENDPOINT_PATH}"
    METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_ENDPOINT_PATH}"
    JSON: ClassVar[dict[str, str]] = {"accept": "application/json"}
    PROTOCOL: ClassVar[dict[str, str]] = {PROTOCOL_VERSION_HEADER: PROTOCOL_VERSION}
    FELIX_DECK = "felix-deck"
    ANNA_DECK = "anna-deck"
    AGENT_BOT = "subject-of-a-service-account"

    def __init__(self, scene: Scene, store: FakeDeckStore, program: TestClient) -> None:
        """Sign both people in through the real callback."""
        self.scene = scene
        self.store = store
        self._program = program
        self.felix = self._browser_of(self.FELIX, "felix")
        self.anna = self._browser_of(self.ANNA, "anna")

    def program(
        self,
        headers: dict[str, str] | None = None,
        *,
        as_browser: TestClient | None = None,
    ) -> TestClient:
        """A program that is no browser: it sends only what it names.

        It is the one client of the world, entered in the app's lifespan, so every
        call meets the front running. It keeps nothing between calls, and carries
        the cookies and headers of `as_browser` when a test names one.
        """
        self._program.cookies.clear()
        self._program.headers.clear()
        if as_browser is not None:
            self._program.cookies.update(as_browser.cookies)
            self._program.headers.update(as_browser.headers)
        self._program.headers.update(headers or {})
        return self._program

    def _browser_of(self, subject: str, username: str) -> TestClient:
        """A browser that signed in through the real callback."""
        browser = self.scene.browser()
        assert self.scene.sign_in(browser, subject=subject, username=username)
        return browser

    def bearer(
        self,
        *,
        subject: str = FELIX,
        groups: list[str] | None = None,
        authorized_party: str = MCP_CLIENT_ID,
        audience: str | None = MCP_URL,
        scope: str | None = MCP_SCOPE,
    ) -> dict[str, str]:
        minted = self.scene.issuer.mint_access_token(
            subject=subject,
            groups=[*MEMBER, MCP_GROUP] if groups is None else groups,
            authorized_party=authorized_party,
            audience=audience,
            scope=scope,
        )
        return {"authorization": f"Bearer {minted}"}

    def own_agent(self, *, groups: list[str]) -> dict[str, str]:
        """The token of a service account that signs in with its own client."""
        minted = self.scene.issuer.mint_access_token(
            subject=self.AGENT_BOT,
            groups=groups,
            authorized_party="a-service-account-client",
            audience=self.MCP_URL,
            scope=MCP_SCOPE,
            agent=(self.FELIX, "notes-bot"),
        )
        return {"authorization": f"Bearer {minted}"}


@contextmanager
def a_world(
    tmp_path: Path,
    *,
    token_clients: frozenset[str] = frozenset(),
    texts: DeckTexts | None = None,
    entering_the_lifespan: bool = True,
) -> Iterator[World]:
    """A world whose app runs its lifespan for as long as the block lasts.

    A test that serves the app through its own lifespan asks for a world that
    does not enter it, since a front runs once.
    """
    with ExitStack() as stack:
        yield _a_world_over(
            tmp_path,
            stack,
            token_clients=token_clients,
            texts=texts,
            entering_the_lifespan=entering_the_lifespan,
        )


def _a_world_over(
    tmp_path: Path,
    stack: ExitStack,
    *,
    token_clients: frozenset[str],
    texts: DeckTexts | None,
    entering_the_lifespan: bool,
) -> World:
    store = FakeDeckStore()
    scene = a_scene(
        tmp_path,
        token_clients=token_clients,
        decks=a_decks(
            GivenDecks(store=store, source=a_configured_source("an-address")),
            FrozenClock(instant=NOW),
        ),
        texts=FakeDeckTexts() if texts is None else texts,
    )
    program = TestClient(
        scene.lobby.app, base_url=A_BROWSERS_ADDRESS, follow_redirects=False
    )
    if entering_the_lifespan:
        stack.enter_context(program)
    world = World(scene, store, program)
    put_a_built_deck(store, slug=World.FELIX_DECK, owner_id=World.FELIX)
    put_a_built_deck(store, slug=World.ANNA_DECK, owner_id=World.ANNA)
    return world


def rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """A request of the served revision: its `_meta` names the revision."""
    meta = {
        PROTOCOL_VERSION_META_KEY: PROTOCOL_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {**(params or {}), "_meta": meta},
    }


def post_mcp(
    program: TestClient,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    version: str | None = PROTOCOL_VERSION,
) -> Response:
    """What a client of the served revision sends: the headers its body implies."""
    sent = {
        **headers,
        "accept": "application/json, text/event-stream",
        MCP_METHOD_HEADER: method,
    }
    if version is not None:
        sent[PROTOCOL_VERSION_HEADER] = version
    if method == "tools/call":
        sent[MCP_NAME_HEADER] = str((params or {})["name"])
    return program.post(MCP_ENDPOINT_PATH, json=rpc(method, params), headers=sent)


def slugs_of(listed: dict[str, Any]) -> list[str]:
    return [deck["slug"] for deck in listed["decks"]]


@dataclass(frozen=True, slots=True)
class ToolAnswer:
    """What an agent's tool call gave back: the text, and whether it is an error."""

    is_error: bool
    text: str


def called_through_mcp(
    program: TestClient,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, Any] | None = None,
) -> ToolAnswer:
    answer = post_mcp(
        program, headers, "tools/call", {"name": name, "arguments": arguments or {}}
    )
    assert answer.status_code == HTTPStatus.OK
    result = answer.json()["result"]
    return ToolAnswer(
        is_error=bool(result.get("isError")), text=result["content"][0]["text"]
    )


def listed_through_mcp(program: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    answer = called_through_mcp(program, headers, "list_decks")
    assert not answer.is_error
    listed: dict[str, Any] = json.loads(answer.text)
    return listed


@dataclass(frozen=True, slots=True)
class MarkedPath:
    """One marked route, the tool that calls it and the felix deck it names."""

    path: str
    tool: str
    arguments: dict[str, Any]


MARKED_PATHS = (
    pytest.param(MarkedPath(DECK_LIST_PATH, "list_decks", {}), id="list"),
    pytest.param(
        MarkedPath(
            DECK_VIEW_PATH.format(slug=World.FELIX_DECK),
            "get_deck",
            {"slug": World.FELIX_DECK},
        ),
        id="deck",
    ),
    pytest.param(
        MarkedPath(
            DECK_TEXT_PATH.format(slug=World.FELIX_DECK),
            "get_deck_text",
            {"slug": World.FELIX_DECK},
        ),
        id="text",
    ),
)


def nothing_was_made(world: World) -> None:
    """No account beyond the two who signed in, and no session beyond theirs."""
    assert (world.scene.count("users"), world.scene.count("sessions")) == (
        World.SIGNED_IN,
        World.SIGNED_IN,
    )
