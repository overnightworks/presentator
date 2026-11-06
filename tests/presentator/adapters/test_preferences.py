"""The settings and preference rows against a temporary SQLite file."""

from pathlib import Path

import pytest

from presentator.adapters.preferences import (
    SqliteInstanceSettingsStore,
    SqlitePersonPreferencesStore,
    create_preference_tables,
)
from presentator.adapters.schema import create_tables
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)

_PERSON = "id-of-felix"
_NEIGHBOUR = "id-of-anna"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database = tmp_path / "preferences.sqlite3"
    create_preference_tables(database)
    return database


def test_an_instance_nobody_configured_has_no_settings_row(database: Path) -> None:
    assert SqliteInstanceSettingsStore(database).read() is None


def test_the_instance_defaults_survive_a_round_trip(database: Path) -> None:
    store = SqliteInstanceSettingsStore(database)
    saved = InstanceSettings(name="Heim", language_tag="de")

    store.write(saved)

    assert store.read() == saved


def test_saving_the_instance_defaults_again_replaces_them(database: Path) -> None:
    store = SqliteInstanceSettingsStore(database)
    store.write(InstanceSettings(name="Heim", language_tag="de"))

    store.write(InstanceSettings(name="Werkstatt", language_tag="en"))

    assert store.read() == InstanceSettings(name="Werkstatt", language_tag="en")


def test_a_person_who_chose_nothing_has_no_preference_row(database: Path) -> None:
    assert SqlitePersonPreferencesStore(database).read(_PERSON) is None


@pytest.mark.parametrize(
    "chosen",
    [
        pytest.param(PersonPreferences(theme=ThemeChoice.LIGHT), id="theme-only"),
        pytest.param(PersonPreferences(), id="follow-system"),
    ],
)
def test_a_persons_choice_survives_a_round_trip(
    database: Path,
    chosen: PersonPreferences,
) -> None:
    store = SqlitePersonPreferencesStore(database)

    store.write(_PERSON, chosen)

    assert store.read(_PERSON) == chosen


def test_one_persons_choice_is_not_another_persons(database: Path) -> None:
    store = SqlitePersonPreferencesStore(database)

    store.write(_PERSON, PersonPreferences(theme=ThemeChoice.DARK))
    store.write(_NEIGHBOUR, PersonPreferences(theme=ThemeChoice.LIGHT))

    assert store.read(_PERSON) == PersonPreferences(theme=ThemeChoice.DARK)
    assert store.read(_NEIGHBOUR) == PersonPreferences(theme=ThemeChoice.LIGHT)


def test_choosing_again_replaces_what_that_person_chose(database: Path) -> None:
    store = SqlitePersonPreferencesStore(database)
    store.write(_PERSON, PersonPreferences(theme=ThemeChoice.DARK))

    store.write(_PERSON, PersonPreferences())

    assert store.read(_PERSON) == PersonPreferences(theme=ThemeChoice.FOLLOW_SYSTEM)


def test_the_first_save_without_a_row_inserts_it(tmp_path: Path) -> None:
    database = tmp_path / "fresh.sqlite3"
    create_tables(database)
    store = SqliteInstanceSettingsStore(database)
    saved = InstanceSettings(name="Heim", language_tag="de")

    store.write(saved)

    assert store.read() == saved
