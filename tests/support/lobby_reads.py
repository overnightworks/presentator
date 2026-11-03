"""What a signed-in client reads from the lobby's JSON routes."""

from http import HTTPStatus

from fastapi.testclient import TestClient

from presentator.api.deck_list_api import DECK_LIST_PATH, DeckListView
from presentator.api.session_api import SESSION_PATH, SessionView


def deck_list(client: TestClient) -> DeckListView:
    """What `GET /api/decks` answers the client's signed-in person."""
    answer = client.get(DECK_LIST_PATH, headers={"accept": "application/json"})
    assert answer.status_code == HTTPStatus.OK
    return DeckListView.model_validate(answer.json())


def signed_in_username(client: TestClient) -> str | None:
    """Who `GET /api/session` says the client is signed in as."""
    answer = client.get(SESSION_PATH, headers={"accept": "application/json"})
    assert answer.status_code == HTTPStatus.OK
    person = SessionView.model_validate(answer.json()).person
    return None if person is None else person.username


def listed_titles(client: TestClient) -> list[str]:
    """The titles `GET /api/decks` lists for the client's signed-in person."""
    return [deck.title for deck in deck_list(client).decks]
