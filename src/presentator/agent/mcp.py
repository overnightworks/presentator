"""The MCP front: the lobby's marked routes, offered as the tools of an account's agent.

The front is the exact route `/mcp` of the lobby app. The bearer gate has already
admitted the agents client's token as that person, or an agent-group token as its own
account, by the time it runs, and every tool call goes through the whole lobby app
again, so it meets the same middleware as any request.
"""

from agent_runtime_client.mcp_server import McpPersonFront, RouteTools, route_tools
from agent_runtime_client.mcp_wire import MCP_ENDPOINT_PATH
from agent_runtime_client.tools import Principal, ToolEffect, agent_tool
from fastapi import FastAPI
from starlette.requests import HTTPConnection
from starlette.routing import Route
from starlette.types import Scope
from webauth.middleware.bearer_gate import (
    MarkedRoute,
    ProtectedResource,
    resource_person,
)
from webauth.policies import AuthorizedParties
from webauth.ports import SignInTransaction

from presentator.agent.settings import McpSettings


def _read_only(name: str) -> dict[str, object]:
    return agent_tool(name=name, effect=ToolEffect.READ_ONLY)


def deck_list_mark() -> dict[str, object]:
    """The mark of the deck list route: the reader's own decks, read only."""
    return _read_only("list_decks")


def deck_view_mark() -> dict[str, object]:
    """The mark of the deck view route: one own deck and its build state, read only."""
    return _read_only("get_deck")


def deck_text_mark() -> dict[str, object]:
    """The mark of the deck text route: one own deck's words, read only."""
    return _read_only("get_deck_text")


def marked_tools(lobby: FastAPI) -> RouteTools[MarkedRoute]:
    """The tools of the lobby's marked routes, which call the complete `lobby`."""
    return route_tools(lobby.openapi(), lobby, mark_route=MarkedRoute)


def install_mcp_front(
    lobby: FastAPI,
    *,
    settings: McpSettings,
    origin: str,
    issuer: str,
    transaction: SignInTransaction,
) -> ProtectedResource:
    """Serve `/mcp` on `lobby` and answer the resource its gate admits tokens for.

    Every schema-bearing route of `lobby` is registered already.
    """
    tools = marked_tools(lobby)

    def person_of(scope: Scope) -> Principal:
        # The identity store keys an account by the issuer's `sub`, which is the id.
        return Principal(iss=issuer, sub=resource_person(HTTPConnection(scope)).id)

    # An exact route, never a mount: the gate decides by the path it sees.
    lobby.router.routes.append(
        Route(
            MCP_ENDPOINT_PATH,
            McpPersonFront(tools.tools, resolve_person=person_of),
        )
    )
    return ProtectedResource(
        url=f"{origin}{MCP_ENDPOINT_PATH}",
        scope=settings.scope,
        required_group=settings.group,
        agent_group=settings.agent_group,
        authorized_parties=AuthorizedParties(frozenset({settings.client_id})),
        transaction=transaction,
        marked_routes=tools.marks,
    )
