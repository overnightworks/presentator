"""Every tool the lobby offers an agent is pinned here."""

from agent_runtime_client.tools import ToolEffect

from presentator.agent.mcp import marked_tools
from tests.support.lobby import a_lobby


def test_the_lobby_offers_exactly_the_pinned_tools() -> None:
    """D7: a route marked as a tool that is not listed here fails the build."""
    offered = marked_tools(a_lobby().app)

    assert {
        (tool.declaration.name, tool.declaration.effect) for tool in offered.tools
    } == {("list_decks", ToolEffect.READ_ONLY)}
    assert {(mark.method, mark.template) for mark in offered.marks} == {
        ("GET", "/api/decks")
    }
