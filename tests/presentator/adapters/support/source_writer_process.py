"""One of two processes that write the same source rows against one Postgres database.

Run with `python -m <module> <action>`. It says it is ready once connected, does
its action when the parent lets it go, and prints what it saw as one JSON line.
"""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.key_drafts import PostgresSourceKeyDrafts
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.decks import SourceWrite
from tests.presentator.adapters.support.source_stores_support import INSTANCE_KEY
from tests.support.postgres import NO_FENCE, engine_from_environment
from tests.support.two_processes import announce_ready_and_wait_for_go

OWNERS: Final = tuple(f"concurrent-owner-{number}" for number in range(20))
_NOON: Final = datetime(2026, 11, 14, 12, tzinfo=UTC)
_REPEATS: Final = 5
NAMES: Final = tuple(f"concurrent-source-{number}" for number in range(20))


@dataclass(frozen=True, slots=True)
class Stores:
    """The stores a child writes through."""

    drafts: PostgresSourceKeyDrafts
    sources: PostgresSourceStore


def a_source_write(name: str, url: str, owner_id: str) -> SourceWrite:
    return SourceWrite(
        name=name,
        url=url,
        ref="main",
        owner_id=owner_id,
        access_secret=f"token of {name}",
        hook_secret_hash=b"digest",
    )


def _get_or_mint(stores: Stores) -> object:
    drafts = stores.drafts
    return {
        owner: drafts.get_or_mint(
            owner, newer_than=_NOON - timedelta(days=1), at=_NOON
        ).id
        for owner in OWNERS
    }


def _mint(stores: Stores) -> object:
    drafts = stores.drafts
    return {
        owner: [drafts.mint(owner, at=_NOON).id for _ in range(_REPEATS)]
        for owner in OWNERS
    }


def _mint_once(stores: Stores) -> object:
    return {owner: stores.drafts.mint(owner, at=_NOON).id for owner in OWNERS}


def _bind(stores: Stores) -> object:
    drafts = stores.drafts
    won: list[str] = []
    for owner in OWNERS:
        draft = drafts.unconsumed_for(owner, newer_than=_NOON - timedelta(days=1))
        if draft is not None and drafts.bind(draft.id, owner_id=owner) is not None:
            won.append(owner)
    return won


def _add_with_one_name(stores: Stores) -> object:
    return [
        name
        for name in NAMES
        if stores.sources.add(
            a_source_write(name, f"https://{os.getpid()}/{name}", OWNERS[0])
        )
        is not None
    ]


def _add_with_one_url(stores: Stores) -> object:
    return [
        name
        for name in NAMES
        if stores.sources.add(
            a_source_write(
                f"{name}-by-{os.getpid()}", f"https://shared/{name}", OWNERS[0]
            )
        )
        is not None
    ]


def _promote(stores: Stores) -> object:
    won: list[str] = []
    for owner in OWNERS:
        draft = stores.drafts.unconsumed_for(
            owner, newer_than=_NOON - timedelta(days=1)
        )
        if draft is None:
            continue
        written = a_source_write(
            f"{owner}-by-{os.getpid()}", f"https://{owner}/{os.getpid()}", owner
        )
        if stores.sources.add_from_draft(written, draft_id=draft.id) is not None:
            won.append(owner)
    return won


ACTIONS: Final[dict[str, Callable[[Stores], object]]] = {
    "add-with-one-name": _add_with_one_name,
    "add-with-one-url": _add_with_one_url,
    "promote": _promote,
    "get-or-mint": _get_or_mint,
    "mint": _mint,
    "mint-once": _mint_once,
    "bind": _bind,
}


def main() -> None:
    """Connect, wait for the go-ahead, do the action, and print its result."""
    action = ACTIONS[sys.argv[1]]
    engine = engine_from_environment()
    stores = Stores(
        drafts=PostgresSourceKeyDrafts(
            engine=engine,
            identifiers=TokenIdentifierFactory(),
            box=secret_box(INSTANCE_KEY),
        ),
        sources=PostgresSourceStore(
            engine=engine,
            identifiers=TokenIdentifierFactory(),
            box=secret_box(INSTANCE_KEY),
            fence=NO_FENCE,
        ),
    )
    announce_ready_and_wait_for_go()
    print(json.dumps(action(stores)))  # noqa: T201 - the parent reads this line
    engine.dispose()


if __name__ == "__main__":
    main()
