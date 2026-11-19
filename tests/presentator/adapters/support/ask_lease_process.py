"""One process of the co-presenter's ask route, its lease taken in the shared Redis.

Run with `python -m <module> <port>` in an environment that names the Redis and
the lease length (`lasts`). Every question is the one admitted session's, and its answer
streams one sentence and then waits until `FINISH_PATH` is posted to this process.
"""

import asyncio
import os
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta
from typing import Final

import logline
import uvicorn
from fastapi import FastAPI

from presentator.adapters.shared_redis import SharedRedis
from presentator.api.admission import SignedInConnection
from presentator.api.copresenter import CoPresenterSurface, add_copresenter_routes
from presentator.contracts.copresenter import AskingPerson
from presentator.contracts.models import Role, User
from tests.presentator.application.support.fakes import (
    FakeSessionRecordStore,
    FakeUserStore,
)
from tests.support.copresenter_support import (
    AN_ANSWER_DEADLINE_SECONDS,
    TALK_ORIGIN,
    PrivateFakes,
    a_use,
    the_admins_decks,
)
from tests.support.lobby import ADMIN

URL_VARIABLE: Final = "ASK_LEASE_REDIS_URL"
LEASE_VARIABLE: Final = "ASK_LEASE_MILLISECONDS"
HEALTH_PATH: Final = "/health"
FINISH_PATH: Final = "/finish"
SESSION: Final = "session-two-processes-ask"


class _HeldAnswers:
    """An answer that streams a sentence, then ends once this process may finish."""

    def __init__(self) -> None:
        self.finish = asyncio.Event()

    @asynccontextmanager
    async def answer(
        self, *, message: str, person: AskingPerson
    ) -> AsyncGenerator[AsyncIterator[str]]:
        del message, person

        async def sentences() -> AsyncIterator[str]:
            yield "Ein Satz."
            await self.finish.wait()
            yield " Noch einer."

        yield sentences()


def _app(redis: SharedRedis, *, lasts: timedelta) -> FastAPI:
    answers = _HeldAnswers()
    app = FastAPI()
    add_copresenter_routes(
        app,
        surface=CoPresenterSurface(
            use=replace(a_use(PrivateFakes(), the_admins_decks()), answers=answers),
            limiter=redis.ask_limiter(lasts=lasts),
            browser_origin=TALK_ORIGIN,
            deadline_seconds=AN_ANSWER_DEADLINE_SECONDS,
        ),
        admission=lambda _connection: SignedInConnection(
            person=User(id=ADMIN, username="admin", role=Role.ADMIN),
            session_id=SESSION,
        ),
        sessions=FakeSessionRecordStore(users=FakeUserStore()),
    )

    app.add_api_route(HEALTH_PATH, lambda: None, methods=["GET"])
    app.add_api_route(FINISH_PATH, answers.finish.set, methods=["POST"])
    return app


async def _serve(port: int) -> None:
    redis = SharedRedis(os.environ[URL_VARIABLE])
    lasts = timedelta(milliseconds=int(os.environ[LEASE_VARIABLE]))
    server = uvicorn.Server(
        uvicorn.Config(
            _app(redis, lasts=lasts), host="127.0.0.1", port=port, log_config=None
        )
    )
    try:
        await server.serve()
    finally:
        await redis.aclose()
        redis.close()


def run() -> None:
    """Serve the ask route on the given port until killed."""
    logline.configure("presentator")
    asyncio.run(_serve(int(sys.argv[1])))


if __name__ == "__main__":
    run()
