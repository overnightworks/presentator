"""The agents client reaches `/mcp` and the marked deck list as one person.

The lobby runs over the real SQLite identity stores and the fake issuer; two
people sign in through the real callback, then a program that is no browser
calls with a bearer alone.
"""

import json
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from agent_runtime_client.mcp_wire import (
    LATEST_PROTOCOL_VERSION,
    MCP_ENDPOINT_PATH,
    PROTOCOL_VERSION_HEADER,
)
from fastapi.testclient import TestClient

from presentator.agent.settings import McpSettings
from presentator.api.deck_list_api import DECK_LIST_PATH
from presentator.api.people import ADMIN_PREFIX
from presentator.api.preference_api import ACCOUNT_PATH
from presentator.host.main import Instance, served_by_host
from presentator.host.polling import Schedule
from tests.presentator.application.support.fakes import FakeDeckStore, FrozenClock
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    NOW,
    GivenDecks,
    a_crowd,
    a_decks,
    a_settings,
    a_talk_app,
    put_a_built_deck,
)
from tests.support.sign_in_support import MEMBER, Scene, a_scene

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
_JSON = {"accept": "application/json"}
_PROTOCOL = {PROTOCOL_VERSION_HEADER: LATEST_PROTOCOL_VERSION.value}
_SETTINGS = McpSettings(client_id=AGENTS, group=GROUP, scope=SCOPE)


class World:
    """A scene whose two people each own one deck, and the deck store behind it."""

    def __init__(self, scene: Scene) -> None:
        """Sign both people in through the real callback."""
        self.scene = scene
        self.felix = self._browser_of(FELIX, "felix")
        self.anna = self._browser_of(ANNA, "anna")

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
            groups=[*MEMBER, GROUP] if groups is None else groups,
            authorized_party=authorized_party,
            audience=audience,
            scope=scope,
        )
        return {"authorization": f"Bearer {minted}"}


def a_world(tmp_path: Path, *, token_clients: frozenset[str] = frozenset()) -> World:
    store = FakeDeckStore()
    scene = a_scene(
        tmp_path,
        token_clients=token_clients,
        decks=a_decks(GivenDecks(store=store), FrozenClock(instant=NOW)),
        mcp=_SETTINGS,
    )
    world = World(scene)
    put_a_built_deck(store, slug="felix-deck", owner_id=FELIX)
    put_a_built_deck(store, slug="anna-deck", owner_id=ANNA)
    return world


@pytest.fixture
def world(tmp_path: Path) -> World:
    return a_world(tmp_path)


def rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


def slugs_of(listed: dict[str, Any]) -> list[str]:
    return [deck["slug"] for deck in listed["decks"]]


def listed_through_mcp(program: TestClient, headers: dict[str, str]) -> list[str]:
    answer = program.post(
        MCP_ENDPOINT_PATH,
        json=rpc("tools/call", {"name": "list_decks", "arguments": {}}),
        headers={**headers, **_PROTOCOL},
    )
    assert answer.status_code == HTTPStatus.OK
    result = answer.json()["result"]
    assert not result.get("isError")
    return slugs_of(json.loads(result["content"][0]["text"]))


def nothing_was_made(world: World) -> None:
    """No account beyond the two who signed in, and no session beyond theirs."""
    assert (world.scene.count("users"), world.scene.count("sessions")) == (
        SIGNED_IN,
        SIGNED_IN,
    )


@pytest.mark.parametrize(
    "token_clients",
    [frozenset[str](), frozenset({HAL})],
    ids=["no token client", "hal"],
)
def test_an_agent_initializes_lists_and_calls_as_the_person_whose_token_it_holds(
    tmp_path: Path, token_clients: frozenset[str]
) -> None:
    """With or without token clients, the front returns only that person's decks."""
    world = a_world(tmp_path, token_clients=token_clients)
    program = world.program()
    headers = world.bearer()

    initialized = program.post(
        MCP_ENDPOINT_PATH,
        json=rpc("initialize", {"protocolVersion": LATEST_PROTOCOL_VERSION.value}),
        headers=headers,
    )
    tools = program.post(
        MCP_ENDPOINT_PATH, json=rpc("tools/list"), headers={**headers, **_PROTOCOL}
    )

    assert initialized.status_code == HTTPStatus.OK
    assert [tool["name"] for tool in tools.json()["result"]["tools"]] == ["list_decks"]
    assert listed_through_mcp(program, headers) == ["felix-deck"]
    assert listed_through_mcp(program, world.bearer(subject=ANNA)) == ["anna-deck"]
    nothing_was_made(world)


def test_a_marked_get_with_the_resource_bearer_is_the_real_route_for_that_person(
    world: World,
) -> None:
    """The route answers whole, owner-filtered, as it does for a person."""
    answer = world.program().get(DECK_LIST_PATH, headers={**world.bearer(), **_JSON})

    assert answer.status_code == HTTPStatus.OK
    assert slugs_of(answer.json()) == ["felix-deck"]
    nothing_was_made(world)


def test_an_ordinary_app_clients_bearer_on_the_marked_get_stays_a_person_token(
    tmp_path: Path,
) -> None:
    """Today's person-token behaviour holds beside the resource."""
    world = a_world(tmp_path, token_clients=frozenset({HAL}))
    token = world.bearer(authorized_party=HAL, audience=None, groups=MEMBER)

    answer = world.program().get(DECK_LIST_PATH, headers={**token, **_JSON})

    assert answer.status_code == HTTPStatus.OK
    assert slugs_of(answer.json()) == ["felix-deck"]


def test_the_resource_bearer_opens_no_admin_delete_or_unmarked_route(
    tmp_path: Path,
) -> None:
    """Only `/mcp` and the marked routes take it."""
    world = a_world(tmp_path, token_clients=frozenset({HAL}))
    bearer = {**world.bearer(), **_JSON}
    program = world.program()

    erased = program.delete(f"{ADMIN_PREFIX}people/{ANNA}", headers=bearer)
    exported = program.get(f"{ADMIN_PREFIX}people/{ANNA}/export", headers=bearer)
    removed = program.delete("/api/sources/anything", headers=bearer)
    unmarked = program.get(ACCOUNT_PATH, headers=bearer)

    assert {erased.status_code, exported.status_code, removed.status_code} <= {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }
    assert unmarked.status_code == HTTPStatus.UNAUTHORIZED
    assert world.scene.count("users") == SIGNED_IN


@pytest.mark.parametrize(
    ("token_clients", "beside_cookie"),
    [
        pytest.param(frozenset[str](), HTTPStatus.OK, id="no token client: ignored"),
        pytest.param(frozenset({HAL}), HTTPStatus.UNAUTHORIZED, id="hal: refused"),
    ],
)
def test_the_resource_bearer_never_authorizes_an_unmarked_route(
    tmp_path: Path, token_clients: frozenset[str], beside_cookie: HTTPStatus
) -> None:
    """Alone it is refused; beside a cookie it is ignored or refused, never used."""
    world = a_world(tmp_path, token_clients=token_clients)
    bearer = world.bearer()

    alone = world.program().get(ACCOUNT_PATH, headers={**bearer, **_JSON})
    with_cookie = world.felix.get(ACCOUNT_PATH, headers=bearer)

    assert alone.status_code == HTTPStatus.UNAUTHORIZED
    assert with_cookie.status_code == beside_cookie


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param({"authorized_party": HAL}, id="wrong client"),
        pytest.param({"audience": "somebody-elses-client"}, id="wrong audience"),
        pytest.param({"scope": None}, id="no scope"),
        pytest.param({"groups": MEMBER}, id="no group"),
        pytest.param({"subject": STRANGER}, id="unknown account"),
    ],
)
def test_a_token_that_does_not_fit_the_resource_is_refused_and_makes_nothing(
    world: World, refusal: dict[str, Any]
) -> None:
    """Refused at `/mcp` and at the marked route; no account or session is created."""
    headers = world.bearer(**refusal)

    through_mcp = world.program().post(
        MCP_ENDPOINT_PATH, json=rpc("tools/list"), headers={**headers, **_PROTOCOL}
    )
    through_route = world.program().get(DECK_LIST_PATH, headers={**headers, **_JSON})

    assert {through_mcp.status_code, through_route.status_code} <= {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }
    nothing_was_made(world)


def test_an_expired_token_is_refused_and_makes_nothing(world: World) -> None:
    headers = world.bearer()
    world.scene.clock.advance(timedelta(hours=1))

    answer = world.program().post(
        MCP_ENDPOINT_PATH, json=rpc("tools/list"), headers={**headers, **_PROTOCOL}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    nothing_was_made(world)


def test_one_persons_bearer_beside_another_persons_cookie_is_the_bearers_person(
    world: World,
) -> None:
    """The resource request never takes the cookie's person."""
    through_route = world.anna.get(DECK_LIST_PATH, headers=world.bearer())
    through_mcp = listed_through_mcp(world.anna, world.bearer())

    assert slugs_of(through_route.json()) == ["felix-deck"]
    assert through_mcp == ["felix-deck"]


def test_an_invalid_resource_bearer_beside_a_valid_cookie_gets_no_cookie_fallback(
    world: World,
) -> None:
    browser = world.felix
    invalid = world.bearer(scope=None)

    through_route = browser.get(DECK_LIST_PATH, headers=invalid)
    through_mcp = browser.post(
        MCP_ENDPOINT_PATH, json=rpc("tools/list"), headers={**invalid, **_PROTOCOL}
    )

    assert through_route.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
    assert through_mcp.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}


def test_cookie_only_reads_work_and_writes_still_need_their_provenance(
    world: World,
) -> None:
    browser = world.felix
    from_another_site = {"sec-fetch-site": "cross-site"}

    read = browser.get(DECK_LIST_PATH, headers=_JSON)
    refused = browser.put(ACCOUNT_PATH, json={}, headers=from_another_site)
    reached = browser.put(ACCOUNT_PATH, json={})

    assert slugs_of(read.json()) == ["felix-deck"]
    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert reached.status_code != HTTPStatus.FORBIDDEN


def test_unauthenticated_discovery_is_public_and_the_front_challenges_for_a_bearer(
    world: World,
) -> None:
    program = world.program()

    metadata = program.get(METADATA_PATH)
    challenge = program.post(MCP_ENDPOINT_PATH, json=rpc("tools/list"))

    assert metadata.status_code == HTTPStatus.OK
    assert metadata.json()["resource"] == MCP_URL
    assert metadata.json()["scopes_supported"] == [SCOPE]
    assert challenge.status_code == HTTPStatus.UNAUTHORIZED
    assert (
        f'resource_metadata="{A_BROWSERS_ADDRESS}{METADATA_PATH}"'
        in (challenge.headers["www-authenticate"])
    )
    assert f'scope="{SCOPE}"' in challenge.headers["www-authenticate"]


def test_without_the_mcp_block_there_is_no_front_and_no_resource(
    tmp_path: Path,
) -> None:
    """A signed-in browser finds neither route: the answers are the framework's 404."""
    scene = a_scene(tmp_path)
    browser = scene.browser()
    scene.sign_in(browser, subject=FELIX, username="felix")

    metadata = browser.get(METADATA_PATH, headers=_JSON)
    front = browser.post(MCP_ENDPOINT_PATH, json=rpc("tools/list"), headers=_JSON)

    assert (metadata.status_code, front.status_code) == (
        HTTPStatus.NOT_FOUND,
        HTTPStatus.NOT_FOUND,
    )


def test_the_production_hostname_routing_keeps_the_front_at_the_lobby_alone(
    world: World,
) -> None:
    """The talk host answers neither the front nor the discovery document."""
    instance = Instance(
        lobby=world.scene.lobby.app,
        talk=a_talk_app(people=a_crowd()),
        poller=Schedule(chore=lambda: None, interval=timedelta(seconds=1)),
        session_sweep=Schedule(chore=lambda: None, interval=timedelta(seconds=1)),
    )
    served = served_by_host(instance, a_settings())
    lobby = TestClient(served, base_url=A_BROWSERS_ADDRESS, follow_redirects=False)
    talk = TestClient(served, base_url=A_TALK_ADDRESS, follow_redirects=False)

    on_lobby = lobby.get(METADATA_PATH)
    on_talk = [
        talk.get(METADATA_PATH, headers=_JSON),
        talk.post(
            MCP_ENDPOINT_PATH,
            json=rpc("tools/list"),
            headers={**world.bearer(), **_PROTOCOL},
        ),
    ]

    assert on_lobby.status_code == HTTPStatus.OK
    assert all(answer.status_code != HTTPStatus.OK for answer in on_talk)
