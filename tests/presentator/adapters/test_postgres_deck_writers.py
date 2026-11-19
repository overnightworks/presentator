"""Two processes writing the same deck, settings and preference rows at once."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.postgres.deck_store import PostgresDeckStore
from presentator.adapters.postgres.sources import PostgresSourceStore
from presentator.adapters.secrets import secret_box
from presentator.contracts.decks import SourceWrite
from tests.presentator.adapters.support.deck_writer_process import (
    OWNER,
    SLUGS,
    SOURCE_NAMES,
    SWITCHED_TALK,
    SWITCHES,
    a_build,
    a_deck,
)
from tests.presentator.adapters.support.source_stores_support import (
    INSTANCE_KEY,
    seed_postgres_owner,
)
from tests.support.postgres import NO_FENCE, ChildModule, engine_on

_CHILD = ChildModule("tests.presentator.adapters.support.deck_writer_process")


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


def _sources_named(engine: Engine, names: tuple[str, ...]) -> tuple[str, ...]:
    seed_postgres_owner(engine, OWNER)
    sources = PostgresSourceStore(
        engine=engine,
        identifiers=TokenIdentifierFactory(),
        box=secret_box(INSTANCE_KEY),
        fence=NO_FENCE,
    )
    written = [
        sources.add(
            SourceWrite(
                name=name,
                url=f"https://git.example.test/{name}.git",
                ref="main",
                owner_id=OWNER,
                access_secret=f"token of {name}",
                hook_secret_hash=b"digest",
            )
        )
        for name in names
    ]
    return tuple(each.id for each in written if each is not None)


@pytest.fixture
def source_ids(engine: Engine) -> tuple[str, str]:
    first, second = _sources_named(engine, SOURCE_NAMES)
    return first, second


def test_two_sources_putting_the_same_slugs_at_once_get_each_slug_once(
    database_url: str, tmp_path: Path, engine: Engine, source_ids: tuple[str, str]
) -> None:
    won_by_first, won_by_second = _CHILD.both_report(
        database_url,
        tmp_path,
        TypeAdapter(list[str]),
        first="put-slugs-as-a",
        second="put-slugs-as-b",
    )

    assert sorted([*won_by_first, *won_by_second]) == sorted(SLUGS)
    with engine.connect() as connection:
        kept = dict(connection.execute(text("SELECT slug, source_id FROM decks")).all())
    assert (
        dict.fromkeys(won_by_first, source_ids[0])
        | dict.fromkeys(won_by_second, source_ids[1])
        == kept
    )


def test_a_reader_beside_a_writer_switching_builds_never_sees_half_a_switch(
    database_url: str, tmp_path: Path, engine: Engine, source_ids: tuple[str, str]
) -> None:
    PostgresDeckStore(engine=engine, fence=NO_FENCE).put(
        a_deck(SWITCHED_TALK, source_ids[0])
    )
    PostgresDeckStore(engine=engine, fence=NO_FENCE).put_build(
        SWITCHED_TALK, a_build(0)
    )

    switched, torn = _CHILD.both_report(
        database_url,
        tmp_path,
        TypeAdapter(object),
        first="switch-builds",
        second="read-builds",
    )

    assert switched == SWITCHES
    assert torn == []


@pytest.mark.usefixtures("source_ids")
def test_two_processes_saving_the_settings_and_a_theme_leave_one_row_each(
    database_url: str, tmp_path: Path, engine: Engine
) -> None:
    first, second = _CHILD.both_report(
        database_url,
        tmp_path,
        TypeAdapter(dict[str, str]),
        first="write-settings-and-theme",
    )

    with engine.connect() as connection:
        settings = connection.execute(
            text("SELECT id, name FROM instance_settings")
        ).all()
        themes = connection.execute(
            text("SELECT user_id, theme FROM person_preferences")
        ).all()
    assert len(settings) == 1
    assert settings[0][1] in {first["name"], second["name"]}
    assert len(themes) == 1
    assert themes[0][1] in {first["theme"], second["theme"]}
