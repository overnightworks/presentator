"""The line each backing store writes when the web first reaches it.

One `store_connection.ended` line per store, with its kind, outcome and
`duration_ms`; the address and the credentials never reach a line.
"""

import logging
from enum import StrEnum

from logline import StepTimer

from presentator.adapters.shared_redis import SharedRedis

_log = logging.getLogger(__name__)


class StoreKind(StrEnum):
    """The backing stores a line names."""

    POSTGRES = "postgres"
    REDIS = "redis"


class StoreFailure(StrEnum):
    """Why reaching a store failed: the closed set of its `reason` codes."""

    NOT_ANSWERING = "not_answering"


def timed_connection(kind: StoreKind) -> StepTimer:
    """The step of reaching one store; the caller refuses or fails it."""
    return StepTimer("store_connection.ended", _log, kind=kind.value)


def reach_redis(redis: SharedRedis) -> None:
    """Ask Redis once and write the line; a Redis that is down does not stop the start.

    The pod stays unready until Redis answers, so the failure is handled here.
    """
    with timed_connection(StoreKind.REDIS) as step:
        step.handled()
        if not redis.is_ready():
            step.fail(StoreFailure.NOT_ANSWERING)
