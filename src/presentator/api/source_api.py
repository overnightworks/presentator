"""The Sources list, a source's page, Fetch now, the key draft and the check, as data.

A source's webhook secret comes back only in the answer to create or renew,
and an access token or private key never comes back, so no answer here carries
one. Instants come from the stored runs; a client words how long ago they were.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, WithJsonSchema

from presentator.api.admission import gated_admin
from presentator.api.hooks import hook_url
from presentator.api.problems import (
    ProblemDetails,
    ProblemError,
    problem_beside_validation,
    problem_response,
)
from presentator.application.decks import RESERVED_SOURCE_NAME, Decks, SourceRefusal
from presentator.contracts.decks import (
    AccessKind,
    ConnectionCheckResult,
    SourceRunFailure,
    SourceRunOutcome,
    SourceState,
)
from presentator.contracts.models import User

_SOURCES: Final = "/api/sources"
_SOURCE: Final = f"{_SOURCES}/{{name}}"
_FETCH: Final = f"{_SOURCE}/fetch"
_ACCESS: Final = f"{_SOURCE}/access"
_KEY_DRAFT: Final = f"{_SOURCES}/{RESERVED_SOURCE_NAME}/key-draft"
_CHECK: Final = f"{_SOURCES}/{RESERVED_SOURCE_NAME}/check"
_REFUSED: Final = HTTPStatus.UNPROCESSABLE_ENTITY
_REFUSED_TYPE: Final = (
    "tag:hallucinai.de,2026-09-08:presentator/problems/source-refused"
)


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


class AccessRenewal(BaseModel):
    """The new HTTPS token, or null for an SSH source, which renews its own pair."""

    model_config = ConfigDict(frozen=True)

    secret: SecretStr | None


class KeyDraftView(BaseModel):
    """An admin's draft deploy key: the id to bind and the half to install."""

    model_config = ConfigDict(frozen=True)

    id: str
    public_key: str


class SourceProbe(BaseModel):
    """The address, access and credential of a source that is not stored yet."""

    model_config = ConfigDict(frozen=True)

    url: str
    access: AccessKind
    secret: SecretStr | None
    key_draft_id: str | None


class ConnectionCheckView(BaseModel):
    """What a probe found; `detail` is git's own first line, not presentator's."""

    model_config = ConfigDict(frozen=True)

    failure: SourceRunFailure | None
    commit: str | None
    detail: str | None
    fingerprint: str | None

    @classmethod
    def of(cls, checked: ConnectionCheckResult) -> ConnectionCheckView:
        """The wire form of the owner's result."""
        return cls(
            failure=checked.failure,
            commit=checked.commit,
            detail=checked.detail,
            fingerprint=checked.fingerprint,
        )


class SourceRefusedProblem(ProblemDetails):
    """A source the application would not take, and the reason code why."""

    type: Annotated[
        str,
        Field(pattern=f"^{re.escape(_REFUSED_TYPE)}$"),
        WithJsonSchema({"type": "string", "const": _REFUSED_TYPE}),
    ] = _REFUSED_TYPE
    reason: Annotated[
        SourceRefusal,
        WithJsonSchema(
            {"type": "string", "enum": [each.value for each in SourceRefusal]}
        ),
    ]


@dataclass(frozen=True, slots=True, kw_only=True)
class _SourceData:
    """Answers the Sources data to an admin, reading through the owner."""

    decks: Decks

    def source_list(self) -> SourceListView:
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

    def source(
        self, request: Request, name: str, admin: Annotated[User, Depends(gated_admin)]
    ) -> SourceView:
        page = self.decks.shown_source(name, viewer=admin.id)
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

    def fetch_now(self, name: str) -> Response:
        if not self.decks.refresh_named(name):
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return Response(status_code=HTTPStatus.NO_CONTENT)

    def renew_access(
        self,
        name: str,
        renewal: AccessRenewal,
        admin: Annotated[User, Depends(gated_admin)],
    ) -> Response:
        secret = "" if renewal.secret is None else renewal.secret.get_secret_value()
        if not self.decks.renew_access(name, secret):
            if self.decks.shown_source(name, viewer=admin.id) is None:
                raise ProblemError(HTTPStatus.NOT_FOUND)
            raise ProblemError(HTTPStatus.UNPROCESSABLE_ENTITY)
        return Response(status_code=HTTPStatus.NO_CONTENT)

    def key_draft(self, admin: Annotated[User, Depends(gated_admin)]) -> KeyDraftView:
        draft = self.decks.source_key_draft(owner_id=admin.id)
        return KeyDraftView(id=draft.id, public_key=draft.public_key)

    def check_connection(
        self, probe: SourceProbe, admin: Annotated[User, Depends(gated_admin)]
    ) -> ConnectionCheckView:
        checked = self.decks.check_connection(
            url=probe.url,
            access=probe.access,
            secret="" if probe.secret is None else probe.secret.get_secret_value(),
            owner_id=admin.id,
            key_draft_id=probe.key_draft_id or "",
        )
        if isinstance(checked, SourceRefusal):
            raise ProblemError(_REFUSED, SourceRefusedProblem(reason=checked))
        return ConnectionCheckView.of(checked)


def source_api_routes(*, decks: Decks) -> APIRouter:
    """The router that answers the Sources data to an admin."""
    data = _SourceData(decks=decks)
    forbidden = problem_response(HTTPStatus.FORBIDDEN)
    not_found = forbidden | problem_response(HTTPStatus.NOT_FOUND)
    router = APIRouter(dependencies=[Depends(gated_admin)])
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
    router.add_api_route(
        _ACCESS,
        data.renew_access,
        methods=["POST"],
        status_code=HTTPStatus.NO_CONTENT,
        responses=not_found,
        openapi_extra=problem_beside_validation(),
    )
    router.add_api_route(
        _KEY_DRAFT, data.key_draft, methods=["POST"], responses=forbidden
    )
    router.add_api_route(
        _CHECK,
        data.check_connection,
        methods=["POST"],
        responses=forbidden,
        openapi_extra=problem_beside_validation(SourceRefusedProblem),
    )
    return router
