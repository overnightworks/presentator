"""The settings and preference rows against a migrated database of the test's own."""

from http import HTTPStatus

import pytest
from sqlalchemy import Engine

from presentator.adapters.postgres.preferences import (
    PostgresInstanceSettingsStore,
    PostgresPersonPreferencesStore,
)
from presentator.application.preferences import Preferences
from presentator.contracts.models import Role, User
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from presentator.contracts.words import SOURCE_LANGUAGE
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    Lobby,
    a_browser_at,
    a_crowd,
    a_lobby_app,
)
from tests.support.people import seed_user

_PERSON = "id-of-felix"
_NEIGHBOUR = "id-of-anna"


def _with_accounts(engine: Engine, *usernames: str) -> Engine:
    for username in usernames:
        seed_user(
            engine, User(id=f"id-of-{username}", username=username, role=Role.USER)
        )
    return engine


@pytest.fixture
def people(engine: Engine) -> PostgresPersonPreferencesStore:
    return PostgresPersonPreferencesStore(_with_accounts(engine, "felix", "anna"))


def test_an_instance_nobody_configured_has_no_settings_row(engine: Engine) -> None:
    assert PostgresInstanceSettingsStore(engine).read() is None


def test_the_instance_defaults_survive_a_round_trip(engine: Engine) -> None:
    store = PostgresInstanceSettingsStore(engine)
    saved = InstanceSettings(name="Heim")

    store.write(saved)

    assert store.read() == saved


def test_saving_the_instance_defaults_again_replaces_them(engine: Engine) -> None:
    store = PostgresInstanceSettingsStore(engine)
    store.write(InstanceSettings(name="Heim"))

    store.write(InstanceSettings(name="Werkstatt"))

    assert store.read() == InstanceSettings(name="Werkstatt")


def test_a_person_who_chose_nothing_has_no_preference_row(
    people: PostgresPersonPreferencesStore,
) -> None:
    assert people.read(_PERSON) is None


@pytest.mark.parametrize(
    "chosen",
    [
        pytest.param(PersonPreferences(theme=ThemeChoice.LIGHT), id="theme-only"),
        pytest.param(PersonPreferences(), id="follow-system"),
    ],
)
def test_a_persons_choice_survives_a_round_trip(
    people: PostgresPersonPreferencesStore,
    chosen: PersonPreferences,
) -> None:
    people.write(_PERSON, chosen)

    assert people.read(_PERSON) == chosen


def test_one_persons_choice_is_not_another_persons(
    people: PostgresPersonPreferencesStore,
) -> None:
    people.write(_PERSON, PersonPreferences(theme=ThemeChoice.DARK))
    people.write(_NEIGHBOUR, PersonPreferences(theme=ThemeChoice.LIGHT))

    assert people.read(_PERSON) == PersonPreferences(theme=ThemeChoice.DARK)
    assert people.read(_NEIGHBOUR) == PersonPreferences(theme=ThemeChoice.LIGHT)


def test_choosing_again_replaces_what_that_person_chose(
    people: PostgresPersonPreferencesStore,
) -> None:
    people.write(_PERSON, PersonPreferences(theme=ThemeChoice.DARK))

    people.write(_PERSON, PersonPreferences())

    assert people.read(_PERSON) == PersonPreferences(theme=ThemeChoice.FOLLOW_SYSTEM)


def test_everybody_reads_the_instances_words_and_their_own_theme(
    engine: Engine,
) -> None:
    chosen = {
        "felix": ThemeChoice.DARK,
        "berta": ThemeChoice.LIGHT,
        "carl": ThemeChoice.FOLLOW_SYSTEM,
    }
    PostgresInstanceSettingsStore(engine).write(InstanceSettings(name="Heim"))
    people = PostgresPersonPreferencesStore(_with_accounts(engine, *chosen))
    for username, theme in chosen.items():
        people.write(f"id-of-{username}", PersonPreferences(theme=theme))
    lobby = _a_lobby_over(engine)

    themes = {}
    for username in [*chosen, "dora"]:
        browser = lobby.another_browser()
        browser.sign_in(username=username)
        body = browser.client.get("/api/session").json()
        themes[username] = ThemeChoice(body["theme"])
        assert body["language"] == SOURCE_LANGUAGE

    assert themes == {**chosen, "dora": ThemeChoice.FOLLOW_SYSTEM}


def test_an_admin_saves_the_settings_over_the_stored_ones(engine: Engine) -> None:
    PostgresInstanceSettingsStore(engine).write(InstanceSettings(name="Heim"))
    lobby = _a_lobby_over(engine)
    lobby.sign_in()

    saved = lobby.client.put("/api/settings", json={"name": "Werkstatt"})

    assert saved.status_code == HTTPStatus.OK
    assert lobby.client.get("/api/settings").json()["name"] == "Werkstatt"


def _a_lobby_over(engine: Engine) -> Lobby:
    people = a_crowd()
    app, _ = a_lobby_app(
        people=people,
        preferences=Preferences(
            instance=PostgresInstanceSettingsStore(engine),
            people=PostgresPersonPreferencesStore(engine),
        ),
    )
    return a_browser_at(
        app,
        people=people,
        base_url=A_BROWSERS_ADDRESS,
        headers=A_BROWSERS_HEADERS,
    )
