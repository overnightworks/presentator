"""The Jinja header's person menu: its theme rows, driven as a browser drives them."""

from http import HTTPStatus

import pytest

from presentator.api.preferences import THEME
from presentator.contracts.preferences import ThemeChoice
from tests.support.lobby import ENGLISH, NEIGHBOUR, Lobby, a_lobby

_A_JINJA_PAGE = "/settings/sources"
_SESSION = "/api/session"


def signed_in_as(lobby: Lobby, username: str) -> None:
    lobby.client.post("/logout")
    lobby.sign_in(username=username)


def the_stored_theme(lobby: Lobby) -> str:
    return str(lobby.client.get(_SESSION).json()["theme"])


@pytest.fixture
def instance() -> Lobby:
    lobby = a_lobby()
    lobby.sign_in()
    return lobby


def test_the_person_menu_offers_the_theme_rows_and_log_out_and_no_link_to_account(
    instance: Lobby,
) -> None:
    page = instance.client.get(_A_JINJA_PAGE).text

    assert ENGLISH.menu_theme in page
    assert ENGLISH.theme_follow_system in page
    assert ENGLISH.theme_light in page
    assert ENGLISH.theme_dark in page
    assert ENGLISH.log_out in page
    assert 'href="/account"' not in page


def test_choosing_dark_in_the_person_menu_stores_that_persons_theme(
    instance: Lobby,
) -> None:
    chosen = instance.client.post(THEME, data={"theme": ThemeChoice.DARK.value})

    assert chosen.status_code == HTTPStatus.NO_CONTENT
    assert chosen.headers["HX-Refresh"] == "true"
    assert the_stored_theme(instance) == ThemeChoice.DARK.value


def test_a_theme_chosen_in_the_menu_leaves_the_neighbours_pages_alone(
    instance: Lobby,
) -> None:
    instance.client.post(THEME, data={"theme": ThemeChoice.DARK.value})

    signed_in_as(instance, NEIGHBOUR)

    assert the_stored_theme(instance) == ThemeChoice.FOLLOW_SYSTEM.value


def test_following_the_system_again_stores_follow_system(
    instance: Lobby,
) -> None:
    instance.client.post(THEME, data={"theme": ThemeChoice.DARK.value})

    instance.client.post(THEME, data={"theme": ThemeChoice.FOLLOW_SYSTEM.value})

    assert the_stored_theme(instance) == ThemeChoice.FOLLOW_SYSTEM.value


def test_the_menu_that_writes_a_theme_carries_the_script_that_posts_it(
    instance: Lobby,
) -> None:
    page = instance.client.get(_A_JINJA_PAGE).text

    assert '<script src="/static/htmx.min.js" defer></script>' in page
    assert instance.client.get("/static/htmx.min.js").status_code == HTTPStatus.OK
