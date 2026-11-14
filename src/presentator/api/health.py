"""The health address both hosts answer, so edge may publish a name.

The answer is a fixed body for any caller. It is 200 while the instance may
serve and 503 while what it serves from cannot answer or is not ready, with a
body that says nothing of why; the reason is in the log.
"""

from collections.abc import Callable
from http import HTTPStatus
from typing import Final

from agents.tools import not_an_agent_tool
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from webauth.policies import PathRules, PublicRule

type Readiness = Callable[[], bool]

HEALTH_PATH: Final = "/health"
HEALTH_IS_PUBLIC: Final = PublicRule(PathRules(exact=frozenset({HEALTH_PATH})))
_HEALTHY: Final = {"status": "ok"}
_UNAVAILABLE: Final = {"status": "unavailable"}


def always_ready() -> bool:
    """The readiness of an instance whose state lives beside it, in one file."""
    return True


def add_health_route(app: FastAPI, readiness: Readiness = always_ready) -> None:
    """Answer `GET /health`: 200 while `readiness` holds, else 503."""

    def health() -> JSONResponse:
        if readiness():
            return JSONResponse(_HEALTHY)
        return JSONResponse(_UNAVAILABLE, status_code=HTTPStatus.SERVICE_UNAVAILABLE)

    app.add_api_route(
        HEALTH_PATH,
        health,
        methods=["GET"],
        openapi_extra=not_an_agent_tool(
            reason="The edge's health probe, not a person's action."
        ),
    )
