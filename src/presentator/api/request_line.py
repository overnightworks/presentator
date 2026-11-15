"""What the log learns of each request: the signed-in person and the client address.

Both decisions are webauth's (its gates admit the person, its trusted proxies
name the client); this module only hands the answers to logline, which writes
the request line. A person's id is the issuer's `sub`.
"""

from dataclasses import dataclass

from logline import set_client_ip, set_sub
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Receive, Scope, Send
from webauth.middleware.session_gate import admitted_user
from webauth.proxies import resolve_client_ip


@dataclass(frozen=True, slots=True)
class ClientAddressNoting:
    """Pure ASGI middleware outside the library stack, so a refusal names its client."""

    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Report the client address the trusted proxies name, then pass on."""
        if scope["type"] == "http":
            set_client_ip(scope, resolve_client_ip(HTTPConnection(scope)))
        await self.app(scope, receive, send)


@dataclass(frozen=True, slots=True)
class SignedInPersonNoting:
    """Pure ASGI middleware inside the gates, so it sees only what they admitted."""

    app: ASGIApp

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Report the person a gate admitted, if any, then pass on."""
        if scope["type"] == "http":
            person = admitted_user(HTTPConnection(scope))
            if person is not None:
                set_sub(scope, person.id)
        await self.app(scope, receive, send)
