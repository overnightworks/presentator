"""The settings and preference rows against a temporary SQLite file."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from presentator.adapters.preferences import (
    SqliteInstanceSettingsStore,
    SqlitePersonPreferencesStore,
    create_preference_tables,
)
from presentator.adapters.schema import create_tables
from presentator.application.preferences import Preferences
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from presentator.contracts.text import Catalogs
from tests.presentator.adapters.support.person_instance_support import (
    a_file_where_people_chose_a_language,
)
from tests.presentator.application.support.fakes import some_words

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
    saved = InstanceSettings(name="Heim", language_tag="de", theme=ThemeChoice.DARK)

    store.write(saved)

    assert store.read() == saved


def test_saving_the_instance_defaults_again_replaces_them(database: Path) -> None:
    store = SqliteInstanceSettingsStore(database)
    store.write(InstanceSettings(name="Heim", theme=ThemeChoice.DARK))

    store.write(InstanceSettings(name="Werkstatt", theme=ThemeChoice.LIGHT))

    assert store.read() == InstanceSettings(
        name="Werkstatt",
        theme=ThemeChoice.LIGHT,
    )


def test_a_person_who_chose_nothing_has_no_preference_row(database: Path) -> None:
    assert SqlitePersonPreferencesStore(database).read(_PERSON) is None


@pytest.mark.parametrize(
    "chosen",
    [
        pytest.param(PersonPreferences(theme=ThemeChoice.LIGHT), id="theme-only"),
        pytest.param(PersonPreferences(), id="back-to-the-instance"),
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

    assert store.read(_PERSON) == PersonPreferences()


_WHO_CHOSE_A_LANGUAGE = "id-of-felix"
_WHO_CHOSE_ONLY_A_LANGUAGE = "id-of-anna"
_WHO_CHOSE_ONLY_A_THEME = "id-of-berta"
_BEFORE_THE_DROP = [
    (_WHO_CHOSE_A_LANGUAGE, "de", ThemeChoice.DARK.value),
    (_WHO_CHOSE_ONLY_A_LANGUAGE, "de", None),
    (_WHO_CHOSE_ONLY_A_THEME, None, ThemeChoice.LIGHT.value),
]
_THEMES_OF_EVERYONE = "SELECT user_id, theme FROM person_preferences ORDER BY user_id"


def an_old_file(directory: Path) -> Path:
    return a_file_where_people_chose_a_language(directory, _BEFORE_THE_DROP)


def columns_of_the_preferences(database: Path) -> list[str]:
    return [
        name
        for (name,) in everything_in(
            database, "SELECT name FROM pragma_table_info('person_preferences')"
        )
    ]


def everything_in(database: Path, statement: str) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as connection, connection:
        return connection.execute(statement).fetchall()


def test_the_start_drops_the_personal_language_and_keeps_every_row_and_theme(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)

    create_tables(database)

    assert columns_of_the_preferences(database) == ["user_id", "theme"]
    assert everything_in(database, _THEMES_OF_EVERYONE) == [
        (_WHO_CHOSE_ONLY_A_LANGUAGE, None),
        (_WHO_CHOSE_ONLY_A_THEME, ThemeChoice.LIGHT.value),
        (_WHO_CHOSE_A_LANGUAGE, ThemeChoice.DARK.value),
    ]


def test_a_second_start_changes_nothing(tmp_path: Path) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    after_the_first = everything_in(database, "SELECT * FROM person_preferences")

    create_tables(database)

    assert everything_in(database, "SELECT * FROM person_preferences") == (
        after_the_first
    )
    assert columns_of_the_preferences(database) == ["user_id", "theme"]


def test_a_fresh_file_and_a_migrated_one_have_the_same_columns(
    tmp_path: Path,
) -> None:
    migrated = an_old_file(tmp_path)
    create_tables(migrated)
    fresh = tmp_path / "fresh.sqlite3"

    create_tables(fresh)

    assert columns_of_the_preferences(fresh) == columns_of_the_preferences(migrated)


def test_after_the_drop_everybody_reads_the_instances_words_and_their_own_theme(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    instance_words = some_words(language_tag="en", language_name="English")
    other_words = some_words(language_tag="de", language_name="Deutsch")
    preferences = Preferences(
        instance=SqliteInstanceSettingsStore(database),
        people=SqlitePersonPreferencesStore(database),
        catalogs=Catalogs(by_tag={"en": instance_words, "de": other_words}),
    )

    both = preferences.appearance_for(_WHO_CHOSE_A_LANGUAGE)
    language_only = preferences.appearance_for(_WHO_CHOSE_ONLY_A_LANGUAGE)

    assert both.text == instance_words
    assert both.theme is ThemeChoice.DARK
    assert language_only.text == instance_words
    assert language_only.theme is preferences.instance_settings().theme
