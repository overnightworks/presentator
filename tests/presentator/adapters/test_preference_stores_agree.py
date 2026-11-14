"""The SQLite and the Postgres settings and preference stores answer the same."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from tests.presentator.adapters.support.deck_stores_support import (
    DeckAdapters,
    postgres_deck_adapters,
    sqlite_deck_adapters,
)
from tests.presentator.adapters.support.source_stores_support import NumberedKeyPairs

_PERSON = "person-é"
_OTHER_PERSON = "other-person"


@pytest.fixture
def sqlite_stores(tmp_path: Path) -> DeckAdapters:
    return sqlite_deck_adapters(tmp_path, NumberedKeyPairs())


@pytest.fixture
def postgres_stores(database_url: str) -> Iterator[DeckAdapters]:
    with postgres_deck_adapters(database_url, NumberedKeyPairs()) as adapters:
        yield adapters


def _write_and_read_settings(adapters: DeckAdapters) -> list[object]:
    """Every `InstanceSettingsStore` method, each observed by what it answers."""
    settings = adapters.settings
    observed: list[object] = [settings.read()]
    for name in ("Heim é", "", "A name with 'quotes' and \"more\""):
        settings.write(InstanceSettings(name=name))
        observed.append(settings.read())
    return observed


def _write_and_read_preferences(adapters: DeckAdapters) -> list[object]:
    """Every `PersonPreferencesStore` method, each observed by what it answers."""
    preferences = adapters.preferences
    for person in (_PERSON, _OTHER_PERSON):
        adapters.sources.seed_owner(person)
    observed: list[object] = [preferences.read(_PERSON), preferences.delete(_PERSON)]
    for theme in ThemeChoice:
        preferences.write(_PERSON, PersonPreferences(theme=theme))
        observed.append(preferences.read(_PERSON))
    preferences.write(_OTHER_PERSON, PersonPreferences(theme=ThemeChoice.DARK))
    observed.extend(
        [
            preferences.delete(_PERSON),
            preferences.delete(_PERSON),
            preferences.read(_PERSON),
            preferences.read(_OTHER_PERSON),
        ]
    )
    return observed


@pytest.mark.parametrize(
    "scenario", [_write_and_read_settings, _write_and_read_preferences]
)
def test_the_same_writes_read_the_same_through_both_adapters(
    scenario: Callable[[DeckAdapters], list[object]],
    sqlite_stores: DeckAdapters,
    postgres_stores: DeckAdapters,
) -> None:
    through_sqlite = scenario(sqlite_stores)
    through_postgres = scenario(postgres_stores)

    assert through_postgres == through_sqlite
    assert any(each is not None for each in through_sqlite)


def test_postgres_preferences_of_an_account_that_does_not_exist_are_an_error(
    postgres_stores: DeckAdapters,
) -> None:
    with pytest.raises(IntegrityError):
        postgres_stores.preferences.write(
            "nobody", PersonPreferences(theme=ThemeChoice.DARK)
        )
