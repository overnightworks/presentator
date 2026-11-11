"""How a page's language and theme resolve: the instance's words, the person's theme."""

import pytest

from presentator.application.preferences import Preferences, UnspokenLanguageError
from presentator.contracts.preferences import (
    Appearance,
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


def _reads_in_the_instances_language(
    lobby: Preferences, user_id: str | None
) -> Appearance:
    return lobby.appearance_for(user_id, language_tag=lobby.served_tag())


def test_an_instance_nobody_configured_speaks_english_and_follows_the_system() -> None:
    appearance = _reads_in_the_instances_language(a_lobby(), _PERSON)

    assert appearance.text == _ENGLISH
    assert appearance.theme is ThemeChoice.FOLLOW_SYSTEM


def test_a_person_without_a_choice_reads_follow_system_in_the_instances_language() -> (
    None
):
    """LB2."""
    lobby = a_lobby(instance=InstanceSettings(language_tag="de"))

    appearance = _reads_in_the_instances_language(lobby, _PERSON)

    assert appearance.text == _GERMAN
    assert appearance.theme is ThemeChoice.FOLLOW_SYSTEM


def test_a_persons_own_choice_is_their_theme_in_the_instances_language() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de"),
        chosen=PersonPreferences(theme=ThemeChoice.LIGHT),
    )

    appearance = _reads_in_the_instances_language(lobby, _PERSON)

    assert appearance.text == _GERMAN
    assert appearance.theme is ThemeChoice.LIGHT


def test_one_persons_choice_leaves_everybody_else_following_the_system() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.DARK))

    neighbour = _reads_in_the_instances_language(lobby, _NEIGHBOUR)

    assert neighbour.text == _ENGLISH
    assert neighbour.theme is ThemeChoice.FOLLOW_SYSTEM


def test_nobody_signed_in_reads_the_instances_language_and_follows_the_system() -> None:
    lobby = a_lobby(
        instance=InstanceSettings(language_tag="de"),
        chosen=PersonPreferences(theme=ThemeChoice.DARK),
    )

    assert _reads_in_the_instances_language(lobby, None).text == _GERMAN
    assert (
        _reads_in_the_instances_language(lobby, None).theme is ThemeChoice.FOLLOW_SYSTEM
    )


def test_an_instance_whose_catalog_file_is_gone_reads_english_again() -> None:
    lobby = a_lobby(instance=InstanceSettings(language_tag="fr"))

    assert _reads_in_the_instances_language(lobby, _PERSON).text == _ENGLISH


def test_saving_the_instance_settings_leaves_a_persons_own_choice_alone() -> None:
    lobby = a_lobby(chosen=PersonPreferences(theme=ThemeChoice.LIGHT))

    lobby.save_instance_settings(InstanceSettings(name="Heim", language_tag="de"))

    assert lobby.instance_settings().name == "Heim"
    assert _reads_in_the_instances_language(lobby, _PERSON).theme is ThemeChoice.LIGHT


def test_every_installed_catalog_is_offered_as_a_language() -> None:
    assert a_lobby().languages() == (
        Language(tag="de", name="Deutsch"),
        Language(tag=_ENGLISH.language_tag, name=_ENGLISH.language_name),
    )


def test_an_instance_with_a_vanished_catalog_can_still_change_the_name() -> None:
    lobby = a_lobby(instance=InstanceSettings(language_tag="xx"))

    lobby.save_instance_settings(InstanceSettings(name="Heim", language_tag="xx"))

    assert lobby.instance_settings().name == "Heim"


def test_saving_an_unspoken_instance_language_is_refused_and_writes_nothing() -> None:
    lobby = a_lobby()
    unspoken = InstanceSettings(language_tag="xx")

    with pytest.raises(UnspokenLanguageError):
        lobby.save_instance_settings(unspoken)

    assert lobby.instance_settings() == InstanceSettings()
