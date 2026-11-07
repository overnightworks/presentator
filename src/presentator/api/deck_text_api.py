"""`GET /api/decks/{slug}/text`: the words of one of the reader's own built decks."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_person
from presentator.api.deck_api import DECK_VIEW_PATH
from presentator.api.problems import ProblemError, problem_response
from presentator.application.own_decks import OwnDecks
from presentator.contracts.copresenter import CoPresenterUnavailableError

DECK_TEXT_PATH: Final = f"{DECK_VIEW_PATH}/text"


class SlideTextView(BaseModel):
    """One slide's words: its number, title, body and speaker notes."""

    model_config = ConfigDict(frozen=True)

    number: int
    title: str
    body: str
    notes: str


class DeckTextView(BaseModel):
    """The words of a deck at its built commit."""

    model_config = ConfigDict(frozen=True)

    title: str
    language: str | None
    slides: list[SlideTextView]


def deck_text_routes(*, own_decks: OwnDecks, mark: Mapping[str, object]) -> APIRouter:
    """The router that answers the words of the reader's own built deck.

    `mark` is the metadata that offers this route to the reader's agent as a tool.
    """
    router = APIRouter()

    def deck_text(request: Request, slug: str) -> DeckTextView:
        """Read the text of one of the reader's own built decks, notes included."""
        try:
            text = own_decks.text_of(slug, viewer=gated_person(request).id)
        except CoPresenterUnavailableError:
            raise ProblemError(HTTPStatus.SERVICE_UNAVAILABLE) from None
        if text is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return DeckTextView(
            title=text.title,
            language=text.language,
            slides=[
                SlideTextView(
                    number=slide.number,
                    title=slide.title,
                    body=slide.body,
                    notes=slide.notes,
                )
                for slide in text.slides
            ],
        )

    router.add_api_route(
        DECK_TEXT_PATH,
        deck_text,
        methods=["GET"],
        responses={
            **problem_response(HTTPStatus.NOT_FOUND),
            **problem_response(HTTPStatus.SERVICE_UNAVAILABLE),
        },
        openapi_extra=dict(mark),
    )
    return router
