"""`GET /api/decks/{slug}`: one deck's facts as data, for the deck page.

The owner answers whose deck it is: another person's deck and an unknown one
raise the same bare problem.
"""

from datetime import datetime
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_person
from presentator.api.problems import ProblemError, problem_response
from presentator.application.decks import Decks
from presentator.contracts.decks import (
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    DeckState,
    talk_address,
)

_DECK: Final = "/api/decks/{slug}"


class FailureView(BaseModel):
    """Why a build failed: a code and its counts, and the toolchain's own words.

    The text is set only for the `toolchain` reason; no sentence of this
    server's is on the wire, the page words each reason itself.
    """

    model_config = ConfigDict(frozen=True)

    reason: BuildFailureReason
    text: str | None
    written_megabytes: int | None
    allowed_megabytes: int | None
    allowed_entries: int | None

    @classmethod
    def of(cls, failure: BuildFailure) -> FailureView:
        """The view of one failure."""
        return cls(
            reason=failure.reason,
            text=failure.text,
            written_megabytes=failure.written_megabytes,
            allowed_megabytes=failure.allowed_megabytes,
            allowed_entries=failure.allowed_entries,
        )


class AttemptView(BaseModel):
    """The build that ran last while it did not become the talk."""

    model_config = ConfigDict(frozen=True)

    commit: str
    started_at: datetime
    outcome: BuildOutcome
    failure: FailureView | None


class DeckView(BaseModel):
    """What a deck page says about one deck, as data."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str
    state: DeckState
    source: str | None
    commit: str
    built_at: datetime | None
    attempt: AttemptView | None
    themes: tuple[str, ...] | None
    talk: str
    views_open: bool


def deck_routes(*, decks: Decks, talk_origin: str) -> APIRouter:
    """The router that answers one deck's view."""
    router = APIRouter()

    @router.get(_DECK, responses=problem_response(HTTPStatus.NOT_FOUND))
    def deck(request: Request, slug: str) -> DeckView:
        page = decks.page(slug, viewer=gated_person(request).id)
        if page is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        attempt = page.attempt
        return DeckView(
            slug=page.slug,
            title=page.title,
            state=page.state,
            source=page.source,
            commit=page.commit,
            built_at=page.built_at,
            attempt=None
            if attempt is None
            else AttemptView(
                commit=attempt.commit,
                started_at=attempt.started_at,
                outcome=attempt.outcome,
                failure=None
                if attempt.failure is None
                else FailureView.of(attempt.failure),
            ),
            themes=page.themes,
            talk=f"{talk_origin}{talk_address(page.slug)}",
            views_open=page.views_open,
        )

    return router
