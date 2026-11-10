"""The MCP front: the lobby's marked routes, offered as the tools of an account's agent.

The front is the exact route `/mcp` of the lobby app. The bearer gate has already
admitted the agents client's token as that person, or an agent-group token as its own
account, by the time it runs, and every tool call goes through the whole lobby app
again, so it meets the same middleware as any request.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from agents.mcp_server import McpPersonFront, RouteTools, route_tools
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.tools import Principal
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

# The realm names the front is built on; no deployment varies them.
# The scope is the one the realm's `mcp-presentator` client scope grants.
MCP_SCOPE = "mcp-presentator"
# The group of people who may use the front, below presentator's login group.
MCP_GROUP = "/apps/presentator/mcp"
# The group of the service accounts that sign in with their own client.
AGENT_GROUP = "/agents"
# The one public client whose tokens act as the person who signed in.
MCP_CLIENT_ID = "agents"


def marked_tools(lobby: FastAPI) -> RouteTools[MarkedRoute]:
    """The tools of the lobby's marked routes, which call the complete `lobby`."""
    return route_tools(lobby.openapi(), lobby, mark_route=MarkedRoute)


def _run_front_with_the_lobby(lobby: FastAPI, front: McpPersonFront) -> None:
    """Enter `front.running()` in the lobby's lifespan, after what it already does.

    The SDK's session manager needs a running task group, and a request outside
    `running()` raises.
    """
    enclosed = lobby.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with enclosed(app), front.running():
            yield

    lobby.router.lifespan_context = lifespan


def install_mcp_front(
    lobby: FastAPI,
    *,
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

    front = McpPersonFront(tools.tools, resolve_person=person_of)
    # An exact route, never a mount: the gate decides by the path it sees.
    lobby.router.routes.append(Route(MCP_ENDPOINT_PATH, front))
    _run_front_with_the_lobby(lobby, front)
    return ProtectedResource(
        url=f"{origin}{MCP_ENDPOINT_PATH}",
        scope=MCP_SCOPE,
        required_group=MCP_GROUP,
        agent_group=AGENT_GROUP,
        authorized_parties=AuthorizedParties(frozenset({MCP_CLIENT_ID})),
        transaction=transaction,
        marked_routes=tools.marks,
    )
