"""The signed-in world the agent tests drive: two people, each with one built deck.

The lobby runs over the real SQLite identity stores and the fake issuer; two
people sign in through the real callback, then a program that is no browser
calls with a bearer alone.
"""

import json
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar

import pytest
from agent_runtime_client.mcp_wire import (
    LATEST_PROTOCOL_VERSION,
    MCP_ENDPOINT_PATH,
    PROTOCOL_VERSION_HEADER,
)
from fastapi.testclient import TestClient

from presentator.agent.settings import McpSettings
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
    AGENTS = "agents-client"
    GROUP = "/apps/presentator/agents"
    SCOPE = "mcp:tools"
    SIGNED_IN = 2
    FELIX = "subject-of-felix"
    ANNA = "subject-of-anna"
    STRANGER = "subject-of-nobody-who-signed-in"
    MCP_URL = f"{A_BROWSERS_ADDRESS}{MCP_ENDPOINT_PATH}"
    METADATA_PATH = f"/.well-known/oauth-protected-resource{MCP_ENDPOINT_PATH}"
    JSON: ClassVar[dict[str, str]] = {"accept": "application/json"}
    PROTOCOL: ClassVar[dict[str, str]] = {
        PROTOCOL_VERSION_HEADER: LATEST_PROTOCOL_VERSION.value
    }
    FELIX_DECK = "felix-deck"
    ANNA_DECK = "anna-deck"
    AGENT_GROUP = "/agents"
    AGENT_BOT = "subject-of-a-service-account"
    SETTINGS = McpSettings(
        client_id=AGENTS, group=GROUP, scope=SCOPE, agent_group=AGENT_GROUP
    )

    def __init__(self, scene: Scene, store: FakeDeckStore) -> None:
        """Sign both people in through the real callback."""
        self.scene = scene
        self.store = store
        self.felix = self._browser_of(self.FELIX, "felix")
        self.anna = self._browser_of(self.ANNA, "anna")

    def program(self, headers: dict[str, str] | None = None) -> TestClient:
        """A program that is no browser: it sends only what it names."""
        return TestClient(
            self.scene.lobby.app,
            base_url=A_BROWSERS_ADDRESS,
            follow_redirects=False,
            headers=headers or {},
        )

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
        authorized_party: str = AGENTS,
        audience: str | None = MCP_URL,
        scope: str | None = SCOPE,
    ) -> dict[str, str]:
        minted = self.scene.issuer.mint_access_token(
            subject=subject,
            groups=[*MEMBER, self.GROUP] if groups is None else groups,
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
            scope=self.SCOPE,
            agent=(self.FELIX, "notes-bot"),
        )
        return {"authorization": f"Bearer {minted}"}


def a_world(
    tmp_path: Path,
    *,
    token_clients: frozenset[str] = frozenset(),
    texts: DeckTexts | None = None,
    mcp_on: bool = True,
) -> World:
    store = FakeDeckStore()
    scene = a_scene(
        tmp_path,
        token_clients=token_clients,
        decks=a_decks(
            GivenDecks(store=store, source=a_configured_source("an-address")),
            FrozenClock(instant=NOW),
        ),
        mcp=World.SETTINGS if mcp_on else None,
        texts=FakeDeckTexts() if texts is None else texts,
    )
    world = World(scene, store)
    put_a_built_deck(store, slug=World.FELIX_DECK, owner_id=World.FELIX)
    put_a_built_deck(store, slug=World.ANNA_DECK, owner_id=World.ANNA)
    return world


def rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


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
    answer = program.post(
        MCP_ENDPOINT_PATH,
        json=rpc("tools/call", {"name": name, "arguments": arguments or {}}),
        headers={**headers, **World.PROTOCOL},
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
