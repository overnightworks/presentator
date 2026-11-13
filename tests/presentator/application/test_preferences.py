"""How a page's theme resolves and how a tag becomes an offered language."""

import pytest

from presentator.application.preferences import Preferences, offered_language
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from presentator.contracts.words import SOURCE_LANGUAGE
from tests.presentator.application.support.fakes import (
    FakeInstanceSettingsStore,
    FakePersonPreferencesStore,
)

_PERSON = "id-of-felix"
_NEIGHBOUR = "id-of-anna"


def a_lobby(
    *,
    instance: InstanceSettings | None = None,
    chosen: PersonPreferences | None = None,
) -> Preferences:
    people = FakePersonPreferencesStore()
    if chosen is not None:
        people.write(_PERSON, chosen)
    return Preferences(
        instance=FakeInstanceSettingsStore(saved=instance),
        people=people,
    )


def test_a_person_without_a_choice_follows_the_system_in_the_language_given() -> None:
    """LB2."""
    appearance = a_lobby().appearance_for(_PERSON, language="en")

    assert appearance.language == "en"
    assert appearance.theme is ThemeChoice.FOLLOW_SYSTEM


def test_a_persons_own_choice_is_their_theme() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.LIGHT))

    assert lobby.appearance_for(_PERSON, language="en").theme is ThemeChoice.LIGHT


def test_one_persons_choice_leaves_everybody_else_following_the_system() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.DARK))

    assert (
        lobby.appearance_for(_NEIGHBOUR, language="en").theme
        is ThemeChoice.FOLLOW_SYSTEM
    )


def test_nobody_signed_in_follows_the_system() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.DARK))

    assert lobby.appearance_for(None, language="en").theme is ThemeChoice.FOLLOW_SYSTEM


def test_saving_the_instance_settings_leaves_a_persons_own_choice_alone() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.LIGHT))

    lobby.save_instance_settings(InstanceSettings(name="Heim"))

    assert lobby.instance_settings().name == "Heim"
    assert lobby.appearance_for(_PERSON, language="en").theme is ThemeChoice.LIGHT


@pytest.mark.parametrize(
    ("tag", "offered"),
    [("de", "de"), ("en", "en"), ("fr", SOURCE_LANGUAGE), ("de-AT", SOURCE_LANGUAGE)],
)
def test_a_tag_is_its_offered_language_else_the_catalog_source(
    tag: str, offered: str
) -> None:
    assert offered_language(tag) == offered
