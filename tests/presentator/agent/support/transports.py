"""Gateway transports that hold a request open or refuse the connection, parked on events."""

import asyncio
from dataclasses import dataclass, field

import httpx2


@dataclass
class HeldTransport(httpx2.AsyncBaseTransport):
    """Takes a request and answers it never, until the test releases it or the caller leaves."""

    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    released: asyncio.Event = field(default_factory=asyncio.Event)
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])
    cancelled: bool = False

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        self.requests.append(request)
        self.arrived.set()
        try:
            await self.released.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return httpx2.Response(500)


class RefusingTransport(httpx2.AsyncBaseTransport):
    """Refuses every connection."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        message = "connection refused"
        raise httpx2.ConnectError(message, request=request)
