"""The MCP front: the lobby's marked routes, offered as the tools of an account's agent.

The front is the exact route `/mcp` of the lobby app. The bearer gate has already
admitted the agents client's token as that person, or an agent-group token as its own
account, by the time it runs, and every tool call goes through the whole lobby app
again, so it meets the same middleware as any request. The lobby is built for the
shared front, so a tool call reaches it through a front of its own, which believes
the same trusted proxies as the host's.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from agents.mcp_server import McpPersonFront, RouteTools, route_tools
from agents.mcp_wire import MCP_ENDPOINT_PATH
from agents.tools import Principal
from fastapi import FastAPI
from logline import set_route
from starlette.requests import HTTPConnection
from starlette.routing import Route
from starlette.types import Receive, Scope, Send
from webauth.admission import GroupPath
from webauth.middleware.bearer_gate import (
    MarkedRoute,
    ProtectedResource,
    resource_person,
)
from webauth.policies import AuthorizedParties
from webauth.proxies import TrustedProxies
from webauth.web_auth import web_auth_front


@dataclass(frozen=True, slots=True)
class _NamedFront:
    """The front, answering as the route `/mcp` in the request line."""

    front: McpPersonFront

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        set_route(scope, MCP_ENDPOINT_PATH)
        await self.front(scope, receive, send)


def marked_tools(
    lobby: FastAPI, *, trusted_proxies: TrustedProxies
) -> RouteTools[MarkedRoute]:
    """The tools of the lobby's marked routes, which call the complete `lobby`."""
    return route_tools(
        lobby.openapi(),
        web_auth_front(lobby, trusted_proxies=trusted_proxies),
        mark_route=MarkedRoute,
    )


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


def install_mcp_front(  # noqa: PLR0913 - the host's one call names each input of the front
    lobby: FastAPI,
    *,
    origin: str,
    issuer: str,
    trusted_proxies: TrustedProxies,
    mcp_scope: str,
    mcp_group: GroupPath,
    agent_group: GroupPath,
    mcp_client_id: str,
) -> ProtectedResource:
    """Serve `/mcp` on `lobby` and answer the resource its gate admits tokens for.

    Every schema-bearing route of `lobby` is registered already. The realm names
    the gate rests on come from the caller: the scope the realm's client scope
    grants, the group of the people who may use the front, the group of the
    service accounts that sign in with their own client, and the one public
    client whose tokens act as the person who signed in.
    """
    tools = marked_tools(lobby, trusted_proxies=trusted_proxies)

    def person_of(scope: Scope) -> Principal:
        # The identity store keys an account by the issuer's `sub`, which is the id.
        return Principal(iss=issuer, sub=resource_person(HTTPConnection(scope)).id)

    front = McpPersonFront(tools.tools, resolve_person=person_of)
    # An exact route, never a mount: the gate decides by the path it sees.
    lobby.router.routes.append(Route(MCP_ENDPOINT_PATH, _NamedFront(front)))
    _run_front_with_the_lobby(lobby, front)
    return ProtectedResource(
        url=f"{origin}{MCP_ENDPOINT_PATH}",
        scope=mcp_scope,
        required_group=mcp_group,
        agent_group=agent_group,
        authorized_parties=AuthorizedParties(frozenset({mcp_client_id})),
        marked_routes=tools.marks,
    )
