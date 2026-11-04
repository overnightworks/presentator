"""How a page's language and theme resolve out of two levels of choice."""

import pytest

from presentator.application.preferences import Preferences, UnspokenLanguageError
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from presentator.contracts.text import DEFAULT_LANGUAGE_TAG, Catalogs, Language
from tests.presentator.application.support.fakes import (
    FakeInstanceSettingsStore,
    FakePersonPreferencesStore,
    some_words,
)

_ENGLISH = some_words(language_tag=DEFAULT_LANGUAGE_TAG, language_name="English")
_GERMAN = some_words(language_tag="de", language_name="Deutsch")
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
        catalogs=Catalogs(by_tag={_ENGLISH.language_tag: _ENGLISH, "de": _GERMAN}),
    )


def test_an_instance_nobody_configured_speaks_english_and_follows_the_system() -> None:
    appearance = a_lobby().appearance_for(_PERSON)

    assert appearance.text == _ENGLISH
    assert appearance.theme is ThemeChoice.FOLLOW_SYSTEM


def test_a_person_without_a_choice_of_their_own_reads_the_instance_default() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de", theme=ThemeChoice.DARK),
    )

    appearance = lobby.appearance_for(_PERSON)

    assert appearance.text == _GERMAN
    assert appearance.theme is ThemeChoice.DARK


def test_a_persons_own_choice_wins_over_the_instance_default() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de", theme=ThemeChoice.DARK),
        chosen=PersonPreferences(theme=ThemeChoice.LIGHT),
    )

    appearance = lobby.appearance_for(_PERSON)

    assert appearance.text == _GERMAN
    assert appearance.theme is ThemeChoice.LIGHT


def test_a_person_who_follows_the_system_reads_follow_system() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(theme=ThemeChoice.DARK),
        chosen=PersonPreferences(theme=ThemeChoice.FOLLOW_SYSTEM),
    )

    assert lobby.appearance_for(_PERSON).theme is ThemeChoice.FOLLOW_SYSTEM


def test_one_persons_choice_leaves_everybody_else_at_the_instance_default() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(theme=ThemeChoice.LIGHT),
        chosen=PersonPreferences(theme=ThemeChoice.DARK),
    )

    neighbour = lobby.appearance_for(_NEIGHBOUR)

    assert neighbour.text == _ENGLISH
    assert neighbour.theme is ThemeChoice.LIGHT


def test_nobody_signed_in_reads_the_instance_default() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de"),
        chosen=PersonPreferences(theme=ThemeChoice.DARK),
    )

    assert lobby.appearance_for(None).text == _GERMAN
    assert lobby.appearance_for(None).theme is ThemeChoice.FOLLOW_SYSTEM


def test_an_instance_whose_catalog_file_is_gone_reads_english_again() -> None:
    lobby = a_lobby(instance=InstanceSettings(language_tag="fr"))

    assert lobby.appearance_for(_PERSON).text == _ENGLISH


def test_saving_the_instance_defaults_leaves_a_persons_own_choice_alone() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.LIGHT))

    lobby.save_instance_settings(
        InstanceSettings(name="Heim", language_tag="de", theme=ThemeChoice.DARK),
    )

    assert lobby.instance_settings().name == "Heim"
    assert lobby.appearance_for(_PERSON).theme is ThemeChoice.LIGHT
    assert lobby.appearance_for(_NEIGHBOUR).theme is ThemeChoice.DARK


def test_a_person_can_hand_their_choice_back_to_the_instance() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de", theme=ThemeChoice.DARK),
        chosen=PersonPreferences(theme=ThemeChoice.LIGHT),
    )

    lobby.save_preferences_of(_PERSON, PersonPreferences())

    assert lobby.preferences_of(_PERSON) == PersonPreferences()
    assert lobby.appearance_for(_PERSON).theme is ThemeChoice.DARK


def test_every_installed_catalog_is_offered_as_a_language() -> None:
    assert a_lobby().languages() == (
        Language(tag="de", name="Deutsch"),
        Language(tag=_ENGLISH.language_tag, name=_ENGLISH.language_name),
    )


def test_an_instance_with_a_vanished_catalog_can_still_change_the_theme() -> None:
    lobby = a_lobby(instance=InstanceSettings(language_tag="xx"))

    lobby.save_instance_settings(
        InstanceSettings(language_tag="xx", theme=ThemeChoice.DARK)
    )

    assert lobby.instance_settings().theme is ThemeChoice.DARK


def test_saving_an_unspoken_instance_language_is_refused_and_writes_nothing() -> None:
    lobby = a_lobby()
    unspoken = InstanceSettings(language_tag="xx")

    with pytest.raises(UnspokenLanguageError):
        lobby.save_instance_settings(unspoken)

    assert lobby.instance_settings() == InstanceSettings()
