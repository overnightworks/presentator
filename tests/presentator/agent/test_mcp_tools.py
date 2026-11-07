"""Every tool the lobby offers an agent is pinned here."""

from agent_runtime_client.tools import AGENT_TOOL_EXTENSION, ToolEffect

from presentator.agent.mcp import marked_tools
from tests.support.lobby import a_lobby


def test_the_lobby_offers_exactly_the_pinned_tools() -> None:
    """D7: a route marked as a tool that is not listed here fails the build."""
    app = a_lobby().app

    marked = {
        (
            method.upper(),
            template,
            node[AGENT_TOOL_EXTENSION]["name"],
            node[AGENT_TOOL_EXTENSION]["effect"],
        )
        for template, operations in app.openapi()["paths"].items()
        for method, node in operations.items()
        if AGENT_TOOL_EXTENSION in node
    }
    offered = marked_tools(app)

    assert marked == {
        ("GET", "/api/decks", "list_decks", ToolEffect.READ_ONLY.value),
        ("GET", "/api/decks/{slug}", "get_deck", ToolEffect.READ_ONLY.value),
        ("GET", "/api/decks/{slug}/text", "get_deck_text", ToolEffect.READ_ONLY.value),
    }
    assert {
        (tool.declaration.name, tool.declaration.effect) for tool in offered.tools
    } == {
        ("list_decks", ToolEffect.READ_ONLY),
        ("get_deck", ToolEffect.READ_ONLY),
        ("get_deck_text", ToolEffect.READ_ONLY),
    }
    assert {(mark.method, mark.template) for mark in offered.marks} == {
        (method, template) for method, template, *_ in marked
    }
