"""The settings and preference rows against a temporary SQLite file."""

import sqlite3
from contextlib import closing
from http import HTTPStatus
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
from tests.presentator.adapters.support.person_instance_support import (
    a_file_where_people_chose_a_language,
)
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    CATALOGS,
    ENGLISH,
    Lobby,
    a_browser_at,
    a_crowd,
    a_lobby_app,
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


_WHO_CHOSE_A_LANGUAGE = "id-of-felix"
_WHO_CHOSE_ONLY_A_LANGUAGE = "id-of-anna"
_WHO_CHOSE_ONLY_A_THEME = "id-of-berta"
_WHO_CHOSE_TO_FOLLOW = "id-of-carl"
_WHO_NEVER_CHOSE = "id-of-dora"
_THE_INSTANCE_BEFORE = ("Heim", "en", ThemeChoice.DARK.value)
_BEFORE_THE_DROP = [
    (_WHO_CHOSE_A_LANGUAGE, "de", ThemeChoice.DARK.value),
    (_WHO_CHOSE_ONLY_A_LANGUAGE, "de", None),
    (_WHO_CHOSE_ONLY_A_THEME, None, ThemeChoice.LIGHT.value),
    (_WHO_CHOSE_TO_FOLLOW, None, ThemeChoice.FOLLOW_SYSTEM.value),
]
_THEMES_OF_EVERYONE = "SELECT user_id, theme FROM person_preferences ORDER BY user_id"
_THE_INSTANCE_ROW = "SELECT * FROM instance_settings"
_COLUMNS = "SELECT name FROM pragma_table_info('{table}')"


def an_old_file(directory: Path) -> Path:
    return a_file_where_people_chose_a_language(
        directory, _BEFORE_THE_DROP, instance=_THE_INSTANCE_BEFORE
    )


def columns_of(database: Path, table: str) -> list[str]:
    return [name for (name,) in everything_in(database, _COLUMNS.format(table=table))]


def everything_in(database: Path, statement: str) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(database)) as connection, connection:
        return connection.execute(statement).fetchall()


def the_old_release_forgets_the_theme_of(database: Path, user_id: str) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE person_preferences SET theme = NULL WHERE user_id = ?", (user_id,)
        )


def the_stored_instance_theme(database: Path) -> str:
    ((theme,),) = everything_in(database, "SELECT theme FROM instance_settings")
    return str(theme)


def test_the_start_drops_the_personal_language_and_folds_only_the_missing_theme(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)

    create_tables(database)

    assert columns_of(database, "person_preferences") == ["user_id", "theme"]
    assert everything_in(database, _THEMES_OF_EVERYONE) == [
        (_WHO_CHOSE_ONLY_A_LANGUAGE, ThemeChoice.FOLLOW_SYSTEM.value),
        (_WHO_CHOSE_ONLY_A_THEME, ThemeChoice.LIGHT.value),
        (_WHO_CHOSE_TO_FOLLOW, ThemeChoice.FOLLOW_SYSTEM.value),
        (_WHO_CHOSE_A_LANGUAGE, ThemeChoice.DARK.value),
    ]


def test_a_second_start_changes_nothing(tmp_path: Path) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    after_the_first = [
        everything_in(database, "SELECT * FROM person_preferences"),
        everything_in(database, _THE_INSTANCE_ROW),
    ]

    create_tables(database)

    assert [
        everything_in(database, "SELECT * FROM person_preferences"),
        everything_in(database, _THE_INSTANCE_ROW),
    ] == after_the_first


def test_a_theme_the_release_before_wrote_as_nothing_is_folded_at_the_next_start(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    the_old_release_forgets_the_theme_of(database, _WHO_CHOSE_ONLY_A_THEME)

    create_tables(database)

    assert (_WHO_CHOSE_ONLY_A_THEME, ThemeChoice.FOLLOW_SYSTEM.value) in (
        everything_in(database, _THEMES_OF_EVERYONE)
    )


def test_the_start_leaves_the_instance_row_as_the_release_before_reads_it(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)

    create_tables(database)

    assert columns_of(database, "instance_settings") == [
        "id",
        "name",
        "language_tag",
        "theme",
    ]
    assert everything_in(database, _THE_INSTANCE_ROW) == [(1, *_THE_INSTANCE_BEFORE)]


@pytest.mark.parametrize("table", ["person_preferences", "instance_settings"])
def test_a_fresh_file_and_a_migrated_one_have_the_same_columns(
    tmp_path: Path, table: str
) -> None:
    migrated = an_old_file(tmp_path)
    create_tables(migrated)
    fresh = tmp_path / "fresh.sqlite3"

    create_tables(fresh)

    assert columns_of(fresh, table) == columns_of(migrated, table)


def test_a_save_over_the_instance_row_keeps_the_theme_the_release_before_reads(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    store = SqliteInstanceSettingsStore(database)
    assert store.read() == InstanceSettings(name="Heim", language_tag="en")

    store.write(InstanceSettings(name="Werkstatt", language_tag="de"))

    assert store.read() == InstanceSettings(name="Werkstatt", language_tag="de")
    assert the_stored_instance_theme(database) == ThemeChoice.DARK.value


def test_the_first_save_without_a_row_stores_a_theme_the_release_before_parses(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fresh.sqlite3"
    create_tables(database)

    SqliteInstanceSettingsStore(database).write(InstanceSettings(name="Heim"))

    ThemeChoice(the_stored_instance_theme(database))


def test_everybody_reads_the_instances_words_and_their_own_theme_after_the_start(
    tmp_path: Path,
) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    lobby = a_lobby_over(database)
    sign_in_as = {
        _WHO_CHOSE_A_LANGUAGE: ThemeChoice.DARK,
        _WHO_CHOSE_ONLY_A_LANGUAGE: ThemeChoice.FOLLOW_SYSTEM,
        _WHO_CHOSE_ONLY_A_THEME: ThemeChoice.LIGHT,
        _WHO_CHOSE_TO_FOLLOW: ThemeChoice.FOLLOW_SYSTEM,
        _WHO_NEVER_CHOSE: ThemeChoice.FOLLOW_SYSTEM,
    }

    themes = {}
    for user_id in sign_in_as:
        browser = lobby.another_browser()
        browser.sign_in(username=user_id.removeprefix("id-of-"))
        body = browser.client.get("/api/session").json()
        themes[user_id] = ThemeChoice(body["theme"])
        assert body["words"]["language_tag"] == ENGLISH.language_tag

    assert themes == sign_in_as
    assert lobby.client.get("/api/session").json()["theme"] == (
        ThemeChoice.FOLLOW_SYSTEM.value
    )


def test_an_admin_saves_the_settings_over_the_migrated_file(tmp_path: Path) -> None:
    database = an_old_file(tmp_path)
    create_tables(database)
    lobby = a_lobby_over(database)
    lobby.sign_in()

    saved = lobby.client.put(
        "/api/settings", json={"name": "Werkstatt", "language_tag": "en"}
    )

    assert saved.status_code == HTTPStatus.OK
    assert lobby.client.get("/api/settings").json()["name"] == "Werkstatt"
    assert the_stored_instance_theme(database) == ThemeChoice.DARK.value


def a_lobby_over(database: Path) -> Lobby:
    people = a_crowd()
    app, _ = a_lobby_app(
        people=people,
        preferences=Preferences(
            instance=SqliteInstanceSettingsStore(database),
            people=SqlitePersonPreferencesStore(database),
            catalogs=CATALOGS,
        ),
    )
    return a_browser_at(
        app,
        people=people,
        base_url=A_BROWSERS_ADDRESS,
        headers=A_BROWSERS_HEADERS,
    )
