"""One of two processes that write the same source rows against one Postgres database.

Run with `python -m <module> <action>`. It says it is ready once connected, does
its action when the parent lets it go, and prints what it saw as one JSON line.
"""

import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Final

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.secrets import secret_box
from tests.presentator.adapters.support.source_stores_support import INSTANCE_KEY
from tests.support.postgres import engine_from_environment
from tests.support.two_processes import announce_ready_and_wait_for_go

OWNERS: Final = tuple(f"concurrent-owner-{number}" for number in range(20))
_NOON: Final = datetime(2026, 11, 14, 12, tzinfo=UTC)
_REPEATS: Final = 5


def _get_or_mint(drafts: PostgresSourceKeyDrafts) -> object:
    return {
        owner: drafts.get_or_mint(
            owner, newer_than=_NOON - timedelta(days=1), at=_NOON
        ).id
        for owner in OWNERS
    }


def _mint(drafts: PostgresSourceKeyDrafts) -> object:
    return {
        owner: [drafts.mint(owner, at=_NOON).id for _ in range(_REPEATS)]
        for owner in OWNERS
    }


def _bind(drafts: PostgresSourceKeyDrafts) -> object:
    won: list[str] = []
    for owner in OWNERS:
        draft = drafts.unconsumed_for(owner, newer_than=_NOON - timedelta(days=1))
        if draft is not None and drafts.bind(draft.id, owner_id=owner) is not None:
            won.append(owner)
    return won


ACTIONS: Final[dict[str, Callable[[PostgresSourceKeyDrafts], object]]] = {
    "get-or-mint": _get_or_mint,
    "mint": _mint,
    "bind": _bind,
}


def main() -> None:
    """Connect, wait for the go-ahead, do the action, and print its result."""
    action = ACTIONS[sys.argv[1]]
    engine = engine_from_environment()
    drafts = PostgresSourceKeyDrafts(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
    )
    announce_ready_and_wait_for_go()
    print(json.dumps(action(drafts)))  # noqa: T201 - the parent reads this line
    engine.dispose()


if __name__ == "__main__":
    main()
