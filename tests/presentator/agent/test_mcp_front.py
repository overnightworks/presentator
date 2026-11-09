"""The agents client reaches `/mcp` and the marked deck list as one person.

The lobby runs over the real SQLite identity stores and the fake issuer; two
people sign in through the real callback, then a program that is no browser
calls with a bearer alone.
"""

import json
from collections.abc import Iterator
from datetime import timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from agent_runtime_client.mcp_wire import MCP_ENDPOINT_PATH, PROTOCOL_VERSION
from fastapi.testclient import TestClient
from mcp_types import UNSUPPORTED_PROTOCOL_VERSION
from webauth.ports import AccountKind

from presentator.agent.mcp import AGENT_GROUP, MCP_GROUP, MCP_SCOPE
from presentator.api.deck_list_api import DECK_LIST_PATH
from presentator.api.people import ADMIN_PREFIX
from presentator.api.preference_api import ACCOUNT_PATH, SETTINGS_PATH
from presentator.contracts.models import Role
from presentator.contracts.preferences import ThemeChoice
from presentator.host.main import Instance, served_by_host
from presentator.host.polling import Schedule
from tests.presentator.agent.support.mcp_world import (
    MARKED_PATHS,
    MarkedPath,
    World,
    a_world,
    called_through_mcp,
    listed_through_mcp,
    nothing_was_made,
    post_mcp,
    slugs_of,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    AN_ISSUER,
    a_crowd,
    a_settings,
    a_talk_app,
)
from tests.support.sign_in_support import ADMIN, MEMBER, a_scene


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    with a_world(tmp_path) as running:
        yield running


def test_one_world_answers_several_requests_through_its_one_running_front(
    world: World,
) -> None:
    first = post_mcp(world.program(), world.bearer(), "tools/list")
    second = post_mcp(world.program(), world.bearer(), "tools/list")

    assert (first.status_code, second.status_code) == (HTTPStatus.OK, HTTPStatus.OK)


@pytest.mark.parametrize(
    "token_clients",
    [frozenset[str](), frozenset({World.HAL})],
    ids=["no token client", "hal"],
)
def test_an_agent_discovers_lists_and_calls_as_the_person_whose_token_it_holds(
    tmp_path: Path, token_clients: frozenset[str]
) -> None:
    """With or without token clients, the front returns only that person's decks."""
    with a_world(tmp_path, token_clients=token_clients) as world:
        program = world.program()
        headers = world.bearer()

        discovered = post_mcp(program, headers, "server/discover")
        tools = post_mcp(program, headers, "tools/list")

        assert discovered.status_code == HTTPStatus.OK
        assert "list_decks" in [
            tool["name"] for tool in tools.json()["result"]["tools"]
        ]
        felix_listed = listed_through_mcp(program, headers)
        anna_listed = listed_through_mcp(program, world.bearer(subject=World.ANNA))
        assert slugs_of(felix_listed) == ["felix-deck"]
        assert (
            felix_listed == world.felix.get(DECK_LIST_PATH, headers=World.JSON).json()
        )
        assert slugs_of(anna_listed) == ["anna-deck"]
        assert anna_listed == world.anna.get(DECK_LIST_PATH, headers=World.JSON).json()
        nothing_was_made(world)


def test_an_own_account_agent_in_presentators_groups_is_admitted_with_no_decks(
    world: World,
) -> None:
    """Its account is created on that first admission, as an agent."""
    headers = world.own_agent(groups=[*MEMBER, MCP_GROUP, AGENT_GROUP])

    listed = listed_through_mcp(world.program(), headers)

    assert listed["decks"] == []
    account = world.scene.users.get(World.AGENT_BOT)
    assert account is not None
    assert account.kind is AccountKind.AGENT
    assert world.scene.count("users") == World.SIGNED_IN + 1
    assert world.scene.count("sessions") == World.SIGNED_IN


@pytest.mark.parametrize(
    "groups",
    [
        pytest.param([*MEMBER, AGENT_GROUP], id="no presentator group"),
        pytest.param([AGENT_GROUP], id="the agent group alone"),
    ],
)
def test_an_own_account_agent_outside_presentators_groups_is_refused_and_makes_nothing(
    world: World, groups: list[str]
) -> None:
    headers = world.own_agent(groups=groups)

    answer = post_mcp(world.program(), headers, "tools/list")

    assert answer.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
    assert world.scene.users.get(World.AGENT_BOT) is None
    nothing_was_made(world)


def test_a_marked_get_with_the_resource_bearer_is_the_real_route_for_that_person(
    world: World,
) -> None:
    """The route answers whole, owner-filtered, as it does for a person."""
    answer = world.program().get(
        DECK_LIST_PATH, headers={**world.bearer(), **World.JSON}
    )

    assert answer.status_code == HTTPStatus.OK
    assert slugs_of(answer.json()) == ["felix-deck"]
    assert answer.json() == world.felix.get(DECK_LIST_PATH, headers=World.JSON).json()
    nothing_was_made(world)


def test_an_ordinary_app_clients_bearer_on_the_marked_get_stays_a_person_token(
    tmp_path: Path,
) -> None:
    """Today's person-token behaviour holds beside the resource."""
    with a_world(tmp_path, token_clients=frozenset({World.HAL})) as world:
        token = world.bearer(authorized_party=World.HAL, audience=None, groups=MEMBER)

        answer = world.program().get(DECK_LIST_PATH, headers={**token, **World.JSON})

        assert answer.status_code == HTTPStatus.OK
        assert slugs_of(answer.json()) == ["felix-deck"]


def test_the_resource_bearer_opens_no_admin_delete_or_excluded_route(
    tmp_path: Path,
) -> None:
    """Only `/mcp` and the marked routes take it."""
    with a_world(tmp_path, token_clients=frozenset({World.HAL})) as world:
        bearer = {**world.bearer(), **World.JSON}
        program = world.program()

        erased = program.delete(f"{ADMIN_PREFIX}people/{World.ANNA}", headers=bearer)
        exported = program.get(
            f"{ADMIN_PREFIX}people/{World.ANNA}/export", headers=bearer
        )
        removed = program.delete("/api/sources/anything", headers=bearer)
        excluded = program.get(World.EXCLUDED_PATH, headers=bearer)

        assert {erased.status_code, exported.status_code, removed.status_code} <= {
            HTTPStatus.UNAUTHORIZED,
            HTTPStatus.FORBIDDEN,
        }
        assert excluded.status_code == HTTPStatus.UNAUTHORIZED
        assert world.scene.count("users") == World.SIGNED_IN


@pytest.mark.parametrize("token_clients", [frozenset[str](), frozenset({World.HAL})])
def test_the_resource_bearer_never_authorizes_an_excluded_route(
    tmp_path: Path, token_clients: frozenset[str]
) -> None:
    """Alone it is refused; beside a cookie it is ignored, never used."""
    with a_world(tmp_path, token_clients=token_clients) as world:
        bearer = world.bearer(subject=World.ANNA)

        alone = world.program().get(
            World.EXCLUDED_PATH, headers={**bearer, **World.JSON}
        )
        with_cookie = world.felix.get(World.EXCLUDED_PATH, headers=bearer)
        cookie_alone = world.felix.get(World.EXCLUDED_PATH)

        assert alone.status_code == HTTPStatus.UNAUTHORIZED
        assert cookie_alone.status_code == HTTPStatus.OK
        assert with_cookie.status_code == cookie_alone.status_code


@pytest.mark.parametrize(
    "refusal",
    [
        pytest.param({"authorized_party": World.HAL}, id="wrong client"),
        pytest.param({"audience": "somebody-elses-client"}, id="wrong audience"),
        pytest.param({"scope": None}, id="no scope"),
        pytest.param({"groups": MEMBER}, id="no group"),
        pytest.param({"subject": World.STRANGER}, id="unknown account"),
    ],
)
def test_a_token_that_does_not_fit_the_resource_is_refused_and_makes_nothing(
    world: World, refusal: dict[str, Any]
) -> None:
    """Refused at `/mcp` and at the marked route; no account or session is created."""
    headers = world.bearer(**refusal)

    through_mcp = post_mcp(world.program(), headers, "tools/list")
    through_route = world.program().get(
        DECK_LIST_PATH, headers={**headers, **World.JSON}
    )

    assert {through_mcp.status_code, through_route.status_code} <= {
        HTTPStatus.UNAUTHORIZED,
        HTTPStatus.FORBIDDEN,
    }
    nothing_was_made(world)


def test_an_expired_token_is_refused_and_makes_nothing(world: World) -> None:
    headers = world.bearer()
    world.scene.clock.advance(timedelta(hours=1))

    answer = post_mcp(world.program(), headers, "tools/list")

    assert answer.status_code == HTTPStatus.UNAUTHORIZED
    nothing_was_made(world)


@pytest.mark.parametrize("marked", MARKED_PATHS)
def test_one_persons_bearer_beside_another_persons_cookie_is_the_bearers_person(
    world: World, marked: MarkedPath
) -> None:
    """The resource request never takes the cookie's person, on every marked path."""
    alone = world.program().get(marked.path, headers={**world.bearer(), **World.JSON})
    through_route = world.anna.get(marked.path, headers=world.bearer())
    through_mcp = called_through_mcp(
        world.program(as_browser=world.anna),
        world.bearer(),
        marked.tool,
        marked.arguments,
    )

    assert alone.status_code == HTTPStatus.OK
    assert through_route.status_code == HTTPStatus.OK
    assert through_route.json() == alone.json()
    assert not through_mcp.is_error
    assert json.loads(through_mcp.text) == alone.json()


@pytest.mark.parametrize("marked", MARKED_PATHS)
def test_an_invalid_resource_bearer_beside_a_valid_cookie_gets_no_cookie_fallback(
    world: World, marked: MarkedPath
) -> None:
    browser = world.felix
    invalid = world.bearer(scope=None)

    through_route = browser.get(marked.path, headers=invalid)
    through_mcp = post_mcp(browser, invalid, "tools/list")

    assert through_route.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}
    assert through_mcp.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}


def test_get_account_and_get_session_answer_the_person_whose_token_the_agent_holds(
    world: World,
) -> None:
    """That person's theme and name, not the cookie's person's."""
    chosen = {"theme": ThemeChoice.DARK.value}
    assert world.felix.put(ACCOUNT_PATH, json=chosen).status_code == HTTPStatus.OK
    program = world.program(as_browser=world.anna)

    account = called_through_mcp(program, world.bearer(), "get_account")
    anna_account = called_through_mcp(
        program, world.bearer(subject=World.ANNA), "get_account"
    )
    session = called_through_mcp(program, world.bearer(), "get_session")

    assert json.loads(account.text) == chosen
    assert json.loads(anna_account.text) != chosen
    assert json.loads(session.text)["person"]["username"] == "felix"


def test_an_admin_persons_agents_client_bearer_never_reads_as_admin(
    world: World,
) -> None:
    """The role is the one the library caps for a bearer request."""
    admin_browser = world.scene.browser()
    assert world.scene.sign_in(
        admin_browser, subject=World.ADMIN, username="root", groups=ADMIN
    )
    headers = world.bearer(subject=World.ADMIN, groups=[*ADMIN, MCP_GROUP])

    session = called_through_mcp(world.program(), headers, "get_session")

    assert session.is_error is False
    person = json.loads(session.text)["person"]
    assert person["username"] == "root"
    assert person["role"] != Role.ADMIN.value


def test_set_account_theme_changes_only_the_theme_of_the_person_whose_token_it_is(
    world: World,
) -> None:
    """Admitted with the bearer alone and no Origin."""
    changed = ThemeChoice.DARK.value
    before_felix = world.felix.get(ACCOUNT_PATH, headers=World.JSON).json()
    before_anna = world.anna.get(ACCOUNT_PATH, headers=World.JSON).json()
    assert before_anna != {"theme": changed}

    written = called_through_mcp(
        world.program(),
        world.bearer(subject=World.ANNA),
        "set_account_theme",
        {"theme": changed},
    )

    assert not written.is_error
    assert json.loads(written.text) == {"theme": changed}
    assert world.anna.get(ACCOUNT_PATH, headers=World.JSON).json() == {"theme": changed}
    assert world.felix.get(ACCOUNT_PATH, headers=World.JSON).json() == before_felix


def test_an_own_account_agent_sets_only_its_own_theme(world: World) -> None:
    """No person's preference changes."""
    headers = world.own_agent(groups=[*MEMBER, MCP_GROUP, AGENT_GROUP])
    before = [
        browser.get(ACCOUNT_PATH, headers=World.JSON).json()
        for browser in (world.felix, world.anna)
    ]
    changed = ThemeChoice.DARK.value
    assert before[0] != {"theme": changed}

    written = called_through_mcp(
        world.program(), headers, "set_account_theme", {"theme": changed}
    )
    read_back = called_through_mcp(world.program(), headers, "get_account")

    assert not written.is_error
    assert json.loads(read_back.text) == {"theme": changed}
    assert [
        browser.get(ACCOUNT_PATH, headers=World.JSON).json()
        for browser in (world.felix, world.anna)
    ] == before


@pytest.mark.parametrize(
    ("method", "path"),
    [
        pytest.param("get", SETTINGS_PATH, id="settings"),
        pytest.param("put", SETTINGS_PATH, id="settings written"),
        pytest.param("get", "/api/sources", id="sources"),
        pytest.param("get", f"{ADMIN_PREFIX}people/{World.ANNA}/export", id="people"),
    ],
)
def test_an_admin_persons_agents_client_bearer_opens_no_admin_route(
    tmp_path: Path, method: str, path: str
) -> None:
    """An agent's token never carries admin rights."""
    with a_world(tmp_path, token_clients=frozenset({World.HAL})) as world:
        headers = world.bearer(groups=[*ADMIN, MCP_GROUP])

        answer = world.program().request(
            method, path, headers={**headers, **World.JSON}, json={}
        )

        assert answer.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}


@pytest.mark.parametrize("token_clients", [frozenset[str](), frozenset({World.HAL})])
def test_an_admin_persons_bearer_beside_a_members_cookie_never_opens_an_admin_route(
    tmp_path: Path, token_clients: frozenset[str]
) -> None:
    """The settings are admin-only: the cookie's member is refused, the bearer ignored."""
    with a_world(tmp_path, token_clients=token_clients) as world:
        admin_browser = world.scene.browser()
        assert world.scene.sign_in(
            admin_browser, subject=World.ADMIN, username="root", groups=ADMIN
        )
        bearer = world.bearer(subject=World.ADMIN, groups=[*ADMIN, MCP_GROUP])

        answer = world.felix.get(SETTINGS_PATH, headers={**bearer, **World.JSON})

        assert answer.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}


def test_a_bearer_on_an_excluded_person_route_is_refused(world: World) -> None:
    """The PDF is a person's route that no tool answers."""
    answer = world.program().get(
        World.EXCLUDED_PATH, headers={**world.bearer(), **World.JSON}
    )

    assert answer.status_code == HTTPStatus.UNAUTHORIZED


def test_cookie_only_reads_work_and_writes_still_need_their_provenance(
    world: World,
) -> None:
    browser = world.felix
    from_another_site = {"sec-fetch-site": "cross-site"}
    before = browser.get(ACCOUNT_PATH, headers=World.JSON).json()
    changed = {"theme": ThemeChoice.DARK.value}
    assert before != changed

    read = browser.get(DECK_LIST_PATH, headers=World.JSON)
    refused = browser.put(ACCOUNT_PATH, json=changed, headers=from_another_site)
    after_refusal = browser.get(ACCOUNT_PATH, headers=World.JSON).json()
    written = browser.put(ACCOUNT_PATH, json=changed)
    after_write = browser.get(ACCOUNT_PATH, headers=World.JSON).json()

    assert slugs_of(read.json()) == ["felix-deck"]
    assert refused.status_code == HTTPStatus.FORBIDDEN
    assert after_refusal == before
    assert written.status_code == HTTPStatus.OK
    assert after_write == changed


def test_unauthenticated_discovery_is_public_and_the_front_challenges_for_a_bearer(
    world: World,
) -> None:
    program = world.program()

    metadata = program.get(World.METADATA_PATH)
    challenge = post_mcp(program, {}, "tools/list")

    assert metadata.status_code == HTTPStatus.OK
    assert metadata.json()["resource"] == World.MCP_URL
    assert metadata.json()["scopes_supported"] == [MCP_SCOPE]
    assert metadata.json()["authorization_servers"] == [AN_ISSUER]
    assert challenge.status_code == HTTPStatus.UNAUTHORIZED
    assert (
        f'resource_metadata="{A_BROWSERS_ADDRESS}{World.METADATA_PATH}"'
        in (challenge.headers["www-authenticate"])
    )
    assert f'scope="{MCP_SCOPE}"' in challenge.headers["www-authenticate"]


def test_a_lobby_with_no_mcp_setting_serves_the_front_and_a_cookie_does_not_open_it(
    tmp_path: Path,
) -> None:
    """A signed-in browser reads the public discovery; the front wants a bearer."""
    scene = a_scene(tmp_path)
    browser = scene.browser()
    scene.sign_in(browser, subject=World.FELIX, username="felix")

    metadata = browser.get(World.METADATA_PATH, headers=World.JSON)
    front = post_mcp(browser, World.JSON, "tools/list")

    assert (metadata.status_code, front.status_code) == (
        HTTPStatus.OK,
        HTTPStatus.UNAUTHORIZED,
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

    on_lobby = lobby.get(World.METADATA_PATH)
    on_talk = [
        talk.get(World.METADATA_PATH, headers=World.JSON),
        post_mcp(talk, world.bearer(), "tools/list"),
    ]

    assert on_lobby.status_code == HTTPStatus.OK
    assert all(answer.status_code != HTTPStatus.OK for answer in on_talk)


def test_a_request_the_gate_admits_outside_a_running_front_fails_loud(
    tmp_path: Path,
) -> None:
    with a_world(tmp_path, entering_the_lifespan=False) as world:
        program = world.program()
        headers = world.bearer()

        with pytest.raises(RuntimeError):
            post_mcp(program, headers, "tools/list")
        challenge = post_mcp(program, {}, "tools/list")

        assert challenge.status_code == HTTPStatus.UNAUTHORIZED


def test_the_served_router_runs_the_front_for_the_lobby_it_serves(
    tmp_path: Path,
) -> None:
    with a_world(tmp_path, entering_the_lifespan=False) as world:
        instance = Instance(
            lobby=world.scene.lobby.app,
            talk=a_talk_app(people=a_crowd()),
            poller=Schedule(chore=lambda: None, interval=timedelta(seconds=1)),
            session_sweep=Schedule(chore=lambda: None, interval=timedelta(seconds=1)),
        )

        with TestClient(
            served_by_host(instance, a_settings()),
            base_url=A_BROWSERS_ADDRESS,
            follow_redirects=False,
        ) as lobby:
            answer = post_mcp(lobby, world.bearer(), "tools/list")

        assert answer.status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    "version",
    [
        pytest.param(None, id="absent"),
        pytest.param("2025-06-18", id="older"),
    ],
)
@pytest.mark.parametrize("method", ["tools/list", "initialize"])
def test_a_request_not_at_the_served_revision_is_refused_naming_the_served_one(
    world: World, method: str, version: str | None
) -> None:
    answer = post_mcp(world.program(), world.bearer(), method, version=version)

    assert answer.status_code == HTTPStatus.BAD_REQUEST
    assert answer.json()["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION
    assert PROTOCOL_VERSION in answer.json()["error"]["data"]["supported"]


def test_initialize_at_the_served_revision_is_refused(world: World) -> None:
    answer = post_mcp(world.program(), world.bearer(), "initialize")

    assert "error" in answer.json()


def test_discovery_offers_tools_and_no_resources(world: World) -> None:
    answer = post_mcp(world.program(), world.bearer(), "server/discover")

    capabilities = answer.json()["result"]["capabilities"]
    assert "tools" in capabilities
    assert "resources" not in capabilities


def test_a_get_on_the_front_is_not_allowed_and_an_origin_is_forbidden(
    world: World,
) -> None:
    program = world.program()

    read = program.get(MCP_ENDPOINT_PATH, headers=world.bearer())
    from_a_page = post_mcp(
        program, {**world.bearer(), "origin": "https://elsewhere.example"}, "tools/list"
    )

    assert read.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert from_a_page.status_code == HTTPStatus.FORBIDDEN
