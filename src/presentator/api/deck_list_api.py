"""`GET /api/decks`: the reader's own decks, as data a page words itself."""

from collections.abc import Mapping
from datetime import datetime
from typing import Final

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_person
from presentator.application.decks import Decks
from presentator.contracts.decks import DeckState

DECK_LIST_PATH: Final = "/api/decks"


class DeckSummary(BaseModel):
    """One of the reader's decks: what it is, how it stands and when it changed."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    state: DeckState
    changed_at: datetime


class DeckListView(BaseModel):
    """The reader's decks, newest change first, and where an empty list points."""

    decks: list[DeckSummary]
    source_address: str | None


def deck_list_routes(*, decks: Decks, mark: Mapping[str, object]) -> APIRouter:
    """The router that answers the reader's deck list.

    `mark` is the metadata that offers this route to the reader's agent as a tool.
    """
    router = APIRouter()

    def deck_list(request: Request) -> DeckListView:
        """List the reader's own decks, newest change first, with their state."""
        viewer = gated_person(request).id
        return DeckListView(
            decks=[
                DeckSummary(
                    slug=deck.slug,
                    title=deck.title,
                    state=deck.state,
                    changed_at=deck.changed_at,
                )
                for deck in decks.listed(viewer=viewer)
            ],
            source_address=decks.source_address(viewer=viewer),
        )

    router.add_api_route(
        DECK_LIST_PATH, deck_list, methods=["GET"], openapi_extra=dict(mark)
    )
    return router
