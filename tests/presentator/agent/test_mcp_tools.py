"""Every tool the lobby offers an agent is pinned here, and every operation is decided."""

import re
from pathlib import Path

import pytest
from agent_runtime_client.tools import AGENT_TOOL_EXTENSION, ToolEffect
from fastapi import FastAPI
from starlette.routing import Match

from presentator.agent.mcp import marked_tools
from tests.presentator.agent.support.mcp_world import World
from tests.support.sign_in_support import a_scene

_METHODS = frozenset({"get", "put", "post", "delete", "patch"})


def operations_of(app: FastAPI) -> list[tuple[str, str, dict[str, dict[str, str]]]]:
    return [
        (method.upper(), template, node)
        for template, operations in app.openapi()["paths"].items()
        for method, node in operations.items()
        if method in _METHODS
    ]


def test_the_lobby_offers_exactly_the_pinned_tools(tmp_path: Path) -> None:
    """D7: a route marked as a tool that is not listed here fails the build."""
    app = a_scene(tmp_path).lobby.app

    marked = {
        (
            method,
            template,
            node[AGENT_TOOL_EXTENSION]["name"],
            node[AGENT_TOOL_EXTENSION]["effect"],
        )
        for method, template, node in operations_of(app)
        if "name" in node[AGENT_TOOL_EXTENSION]
    }
    offered = marked_tools(app)

    assert marked == {
        ("GET", "/api/decks", "list_decks", ToolEffect.READ_ONLY.value),
        ("GET", "/api/decks/{slug}", "get_deck", ToolEffect.READ_ONLY.value),
        ("GET", "/api/decks/{slug}/text", "get_deck_text", ToolEffect.READ_ONLY.value),
        ("GET", "/api/session", "get_session", ToolEffect.READ_ONLY.value),
        ("GET", "/api/account", "get_account", ToolEffect.READ_ONLY.value),
        ("PUT", "/api/account", "set_account_theme", ToolEffect.SIDE_EFFECTING.value),
    }
    assert {
        (tool.declaration.name, tool.declaration.effect) for tool in offered.tools
    } == {(name, ToolEffect(effect)) for _, _, name, effect in marked}
    assert {(mark.method, mark.template) for mark in offered.marks} == {
        (method, template) for method, template, *_ in marked
    }


@pytest.mark.parametrize("token_clients", [frozenset[str](), frozenset({World.HAL})])
def test_the_lobby_starts_with_every_operation_decided_with_and_without_token_clients(
    tmp_path: Path, token_clients: frozenset[str]
) -> None:
    """T1: a forgotten decision stops the start, so a built lobby holds none."""
    app = a_scene(tmp_path, token_clients=token_clients).lobby.app

    operations = operations_of(app)

    assert operations
    assert all(AGENT_TOOL_EXTENSION in node for _, _, node in operations)
    admin_people = [path for _, path, _ in operations if "/api/admin/" in path]
    assert bool(admin_people) is bool(token_clients)


def test_no_marked_template_is_met_by_another_route_of_its_method(
    tmp_path: Path,
) -> None:
    """T14: a marked path reads as itself and nothing else of its method can answer it."""
    app = a_scene(tmp_path).lobby.app
    marked = [
        (method, template)
        for method, template, node in operations_of(app)
        if "name" in node[AGENT_TOOL_EXTENSION]
    ]

    for method, template in marked:
        concrete = re.sub(r"\{[^}]*\}", "a-value", template)
        scope = {"type": "http", "method": method, "path": concrete, "root_path": ""}
        answering = [
            route.path
            for route in app.router.routes
            if route.matches(scope)[0] is Match.FULL
        ]
        assert answering == [template], (method, template)
