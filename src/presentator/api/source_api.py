"""The Sources list, a source's page and Fetch now as data, for an admin.

A source's webhook secret comes back only in the answer to create or renew,
so no answer here carries one. Instants come from the stored runs; a client
words how long ago they were.
"""

from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Final

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict

from presentator.api.admission import gated_person
from presentator.api.hooks import hook_url
from presentator.api.problems import ProblemError, problem_response
from presentator.application.decks import Decks
from presentator.contracts.decks import (
    AccessKind,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)

_SOURCES: Final = "/api/sources"
_SOURCE: Final = f"{_SOURCES}/{{name}}"
_FETCH: Final = f"{_SOURCE}/fetch"


class SourceSummary(BaseModel):
    """One source of the list: where it is, how it stands and when it was fetched."""

    model_config = ConfigDict(frozen=True)

    name: str
    url: str
    access: AccessKind | None
    state: SourceState
    fetched_at: datetime | None


class SourceListView(BaseModel):
    """Every source of the instance, in the owner's order."""

    sources: list[SourceSummary]


class SourceRunView(BaseModel):
    """One poll of a source: when, how it ended and why it did not succeed."""

    model_config = ConfigDict(frozen=True)

    outcome: SourceRunOutcome
    state: SourceState
    at: datetime
    commit: str | None
    reason: SourceRunFailure | None


class SourceDeckView(BaseModel):
    """A deck the source carries."""

    model_config = ConfigDict(frozen=True)

    slug: str
    title: str


class SourceView(SourceSummary):
    """One source with its newest runs and the viewer's own decks from it."""

    hook_url: str
    public_key: str | None
    runs: list[SourceRunView]
    decks: list[SourceDeckView]


@dataclass(frozen=True, slots=True, kw_only=True)
class _SourceData:
    """Answers the Sources data to an admin, reading through the owner."""

    decks: Decks

    def source_list(self, request: Request) -> SourceListView:
        _admin_id(request)
        return SourceListView(
            sources=[
                SourceSummary(
                    name=source.name,
                    url=source.url,
                    access=source.access,
                    state=source.state,
                    fetched_at=source.fetched_at,
                )
                for source in self.decks.listed_sources()
            ]
        )

    def source(self, request: Request, name: str) -> SourceView:
        page = self.decks.shown_source(name, viewer=_admin_id(request))
        if page is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return SourceView(
            name=page.name,
            url=page.url,
            access=page.access,
            state=page.state,
            fetched_at=page.fetched_at,
            hook_url=hook_url(request, page.name),
            public_key=page.public_key,
            runs=[
                SourceRunView(
                    outcome=run.outcome,
                    state=run.state,
                    at=run.at,
                    commit=run.commit,
                    reason=run.reason,
                )
                for run in page.runs
            ],
            decks=[
                SourceDeckView(slug=deck.slug, title=deck.title) for deck in page.decks
            ],
        )

    def fetch_now(self, request: Request, name: str) -> Response:
        _admin_id(request)
        if not self.decks.refresh_named(name):
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return Response(status_code=HTTPStatus.NO_CONTENT)


def _admin_id(request: Request) -> str:
    person = gated_person(request)
    if not person.is_admin:
        raise ProblemError(HTTPStatus.FORBIDDEN)
    return person.id


def source_api_routes(*, decks: Decks) -> APIRouter:
    """The router that answers the Sources data to an admin."""
    data = _SourceData(decks=decks)
    forbidden = problem_response(HTTPStatus.FORBIDDEN)
    not_found = forbidden | problem_response(HTTPStatus.NOT_FOUND)
    router = APIRouter()
    router.add_api_route(
        _SOURCES, data.source_list, methods=["GET"], responses=forbidden
    )
    router.add_api_route(_SOURCE, data.source, methods=["GET"], responses=not_found)
    router.add_api_route(
        _FETCH,
        data.fetch_now,
        methods=["POST"],
        status_code=HTTPStatus.NO_CONTENT,
        responses=not_found,
    )
    return router
