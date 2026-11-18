"""One of two processes that write the same deck, settings and preference rows.

Run with `python -m <module> <action>`. It says it is ready once connected, does
its action when the parent lets it go, and prints what it saw as one JSON line.
"""

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from sqlalchemy import Engine

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.decks import Build, BuildRun, Deck
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from tests.presentator.adapters.support.source_stores_support import INSTANCE_KEY
from tests.support.postgres import NO_FENCE, engine_from_environment
from tests.support.two_processes import announce_ready_and_wait_for_go

OWNER: Final = "deck-writer-owner"
SOURCE_NAMES: Final = ("deck-writer-a", "deck-writer-b")
SLUGS: Final = tuple(f"concurrent-talk-{number}" for number in range(40))
SWITCHED_TALK: Final = "switched-talk"
SWITCHES: Final = 200
MARKED_SOURCE_NAMES: Final = tuple(f"marked-source-{number}" for number in range(40))
_DECKS_PER_MARKED_SOURCE: Final = 20
_ROUNDS: Final = 5
_NOON: Final = datetime(2026, 11, 14, 12, tzinfo=UTC)
_THEMES: Final = (ThemeChoice.LIGHT, ThemeChoice.DARK)


@dataclass(frozen=True, slots=True)
class Stores:
    """The stores a child writes through."""

    decks: PostgresDeckStore
    sources: PostgresSourceStore
    settings: PostgresInstanceSettingsStore
    preferences: PostgresPersonPreferencesStore


def a_deck(slug: str, source_id: str) -> Deck:
    return Deck(
        slug=slug,
        title=f"{slug} from {source_id}",
        changed_at=_NOON,
        owner_id=OWNER,
        source_id=source_id,
        commit="commit-0",
        build=None,
        attempt=None,
    )


def a_build(number: int) -> Build:
    return Build(
        run=BuildRun(f"run-{number}"),
        directory=Path(f"/builds/{number}"),
        pdf=Path(f"/builds/{number}.pdf"),
        commit=f"commit-{number}",
        built_at=_NOON + timedelta(minutes=number),
        toolchain=None,
    )


def _source_id(stores: Stores, name: str) -> str:
    return next(each.id for each in stores.sources.all() if each.name == name)


def _put_slugs_as(name: str) -> Callable[[Stores], object]:
    def put(stores: Stores) -> object:
        source_id = _source_id(stores, name)
        return [slug for slug in SLUGS if stores.decks.put(a_deck(slug, source_id))]

    return put


def _switch_builds(stores: Stores) -> object:
    for number in range(SWITCHES):
        stores.decks.put_build(SWITCHED_TALK, a_build(number))
    return SWITCHES


def _read_builds(stores: Stores) -> object:
    """Every read that is not one whole build's columns, from the first to the last."""
    torn: list[str] = []
    while True:
        deck = stores.decks.get(SWITCHED_TALK)
        assert deck is not None
        build = deck.build
        assert build is not None
        number = int(build.commit.removeprefix("commit-"))
        if build != a_build(number):
            torn.append(f"{build}")
        if number == SWITCHES - 1:
            return torn


def marked_slugs(source_name: str, *, first_half: bool) -> tuple[str, ...]:
    """Half of the slugs a marked source carries."""
    slugs = tuple(
        f"{source_name}-talk-{number}" for number in range(_DECKS_PER_MARKED_SOURCE)
    )
    middle = len(slugs) // 2
    return slugs[:middle] if first_half else slugs[middle:]


def _mark_as(*, first_half: bool) -> Callable[[Stores], object]:
    def mark(stores: Stores) -> object:
        for name in MARKED_SOURCE_NAMES:
            stores.decks.mark_removed_except(
                frozenset(marked_slugs(name, first_half=first_half)),
                source_id=_source_id(stores, name),
                at=_NOON,
            )
        return len(MARKED_SOURCE_NAMES)

    return mark


def _write_settings_and_theme(stores: Stores) -> object:
    name = f"name-of-{os.getpid()}"
    theme = _THEMES[os.getpid() % len(_THEMES)]
    for _ in range(_ROUNDS * 10):
        stores.settings.write(InstanceSettings(name=name))
        stores.preferences.write(OWNER, PersonPreferences(theme=theme))
    return {"name": name, "theme": theme.value}


ACTIONS: Final[dict[str, Callable[[Stores], object]]] = {
    "put-slugs-as-a": _put_slugs_as(SOURCE_NAMES[0]),
    "put-slugs-as-b": _put_slugs_as(SOURCE_NAMES[1]),
    "switch-builds": _switch_builds,
    "read-builds": _read_builds,
    "mark-the-first-half-carried": _mark_as(first_half=True),
    "mark-the-second-half-carried": _mark_as(first_half=False),
    "write-settings-and-theme": _write_settings_and_theme,
}


def _stores_on(engine: Engine) -> Stores:
    return Stores(
        decks=PostgresDeckStore(engine=engine, fence=NO_FENCE),
        sources=PostgresSourceStore(
            engine=engine,
            identifiers=TokenIdentifierFactory(),
            box=secret_box(INSTANCE_KEY),
            fence=NO_FENCE,
        ),
        settings=PostgresInstanceSettingsStore(engine),
        preferences=PostgresPersonPreferencesStore(engine),
    )


def main() -> None:
    """Connect, wait for the go-ahead, do the action, and print its result."""
    action = ACTIONS[sys.argv[1]]
    engine = engine_from_environment()
    stores = _stores_on(engine)
    announce_ready_and_wait_for_go()
    print(json.dumps(action(stores)))  # noqa: T201 - the parent reads this line
    engine.dispose()


if __name__ == "__main__":
    main()
