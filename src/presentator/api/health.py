"""The health address both hosts answer, so edge may publish a name.

A static answer: an instance that serves at all has opened and migrated its
database and reached its build worker before it bound the port, so the route
checks nothing per request and a busy database cannot make it flap.
"""

from typing import Final

from fastapi import FastAPI
from webauth.policies import PathRules, PublicRule

HEALTH_PATH: Final = "/health"
HEALTH_IS_PUBLIC: Final = PublicRule(PathRules(exact=frozenset({HEALTH_PATH})))
_HEALTHY: Final = {"status": "ok"}


def add_health_route(app: FastAPI) -> None:
    """Answer `GET /health` with a fixed body, for any caller."""
    app.add_api_route(HEALTH_PATH, _health, methods=["GET"])


def _health() -> dict[str, str]:
    return dict(_HEALTHY)
