"""Gateway transports that hold a request open or refuse the connection."""

import asyncio

import httpx2


class HeldTransport(httpx2.MockTransport):
    """Takes a request and never answers it, until the caller leaves."""

    def __init__(self) -> None:
        """Start with no request held."""
        super().__init__(self._hold)
        self.arrived = asyncio.Event()
        self.requests: list[httpx2.Request] = []
        self.cancelled = False

    async def _hold(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.arrived.set()
        try:
            await asyncio.get_running_loop().create_future()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return httpx2.Response(500)


class RefusingTransport(httpx2.MockTransport):
    """Refuses every connection."""

    def __init__(self) -> None:
        """Start with no request seen."""
        super().__init__(self._refuse)
        self.requests: list[httpx2.Request] = []

    async def _refuse(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        message = "connection refused"
        raise httpx2.ConnectError(message, request=request)
