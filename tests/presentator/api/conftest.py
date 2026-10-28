"""The lobbies every route test starts from."""

import pytest

from tests.support.lobby import Lobby, a_lobby


@pytest.fixture
def lobby() -> Lobby:
    return a_lobby()


@pytest.fixture
def signed_in_lobby(lobby: Lobby) -> Lobby:
    lobby.sign_in()
    return lobby
