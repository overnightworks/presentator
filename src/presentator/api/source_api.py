"""The Sources list and page, Fetch now, the key draft, check, create and removal.

A source's webhook secret comes back only in the answer to create or renew,
and an access token or private key never comes back, so no answer here carries
one. Instants come from the stored runs; a client words how long ago they were.
"""

from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from typing import Annotated, Final, NamedTuple

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, SecretStr, WithJsonSchema

from presentator.api.admission import gated_admin
from presentator.api.hooks import hook_url
from presentator.api.problems import (
    FixedType,
    ProblemDetails,
    ProblemError,
    problem_beside_validation,
    problem_response,
)
from presentator.application.decks import (
    RESERVED_SOURCE_NAME,
    Decks,
    NewSourceDraft,
    SourceRefusal,
    SourceRemovalRefusal,
    SourceRemovalStale,
)
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
_WEBHOOK_SECRET: Final = f"{_SOURCE}/webhook-secret"
_REMOVAL: Final = f"{_SOURCE}/removal"
_KEY_DRAFT: Final = f"{_SOURCES}/{RESERVED_SOURCE_NAME}/key-draft"
_CHECK: Final = f"{_SOURCES}/{RESERVED_SOURCE_NAME}/check"
_REFUSED: Final = HTTPStatus.UNPROCESSABLE_ENTITY
_REFUSED_TYPE: Final = (
    "tag:hallucinai.de,2026-09-08:presentator/problems/source-refused"
)
_STALE_TYPE: Final = (
    "tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-stale"
)
_REMOVAL_REFUSED_TYPE: Final = (
    "tag:hallucinai.de,2026-09-08:presentator/problems/source-removal-refused"
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

    def in_the_owners_form(self) -> OwnersForm:
        """The secret and the draft id, null as "", as the fingerprint binds them."""
        return OwnersForm(_owners_secret(self.secret), self.key_draft_id or "")


class OwnersForm(NamedTuple):
    """A probe's credential and key draft as the owner takes them: text only."""

    secret: str
    key_draft_id: str


def _owners_secret(secret: SecretStr | None) -> str:
    return "" if secret is None else secret.get_secret_value()


class NewSource(SourceProbe):
    """A checked source to store: the probe, its name and the check's fingerprint."""

    name: str
    fingerprint: str


class CreatedSourceView(BaseModel):
    """The new source and the webhook secret, which no other answer carries."""

    model_config = ConfigDict(frozen=True)

    name: str
    hook_url: str
    webhook_secret: str


class WebhookSecretView(BaseModel):
    """The renewed webhook secret, which no other answer carries."""

    model_config = ConfigDict(frozen=True)

    webhook_secret: str


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

    type: Annotated[str, FixedType(_REFUSED_TYPE)] = _REFUSED_TYPE
    reason: Annotated[
        SourceRefusal,
        WithJsonSchema(
            {"type": "string", "enum": [each.value for each in SourceRefusal]}
        ),
    ]


class SourceRemovalStaleProblem(ProblemDetails):
    """The counts a removal was confirmed with no longer stand: the current ones."""

    type: Annotated[str, FixedType(_STALE_TYPE)] = _STALE_TYPE
    deck_count: int
    run_count: int


class SourceRemovalRefusedProblem(ProblemDetails):
    """A removal that deleted no row, and the reason code why."""

    type: Annotated[str, FixedType(_REMOVAL_REFUSED_TYPE)] = _REMOVAL_REFUSED_TYPE
    reason: Annotated[
        SourceRemovalRefusal,
        WithJsonSchema(
            {"type": "string", "enum": [each.value for each in SourceRemovalRefusal]}
        ),
    ]


class SourceRemovalPreview(BaseModel):
    """What removing a source takes with it: its decks and the runs it keeps."""

    model_config = ConfigDict(frozen=True)

    deck_count: int
    run_count: int


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

    def removal(self, name: str) -> SourceRemovalPreview:
        preview = self.decks.removal(name)
        if preview is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return SourceRemovalPreview(
            deck_count=preview.deck_count, run_count=preview.run_count
        )

    def remove(
        self,
        name: str,
        deck_count: Annotated[int, Query(ge=0)],
        run_count: Annotated[int, Query(ge=0)],
    ) -> Response:
        removed = self.decks.remove_source(
            name, expected_deck_count=deck_count, expected_run_count=run_count
        )
        if removed is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        if isinstance(removed, SourceRemovalRefusal):
            raise ProblemError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                SourceRemovalRefusedProblem(reason=removed),
            )
        if isinstance(removed, SourceRemovalStale):
            raise ProblemError(
                HTTPStatus.CONFLICT,
                SourceRemovalStaleProblem(
                    deck_count=removed.current.deck_count,
                    run_count=removed.current.run_count,
                ),
            )
        return Response(status_code=HTTPStatus.NO_CONTENT)

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
        if not self.decks.renew_access(name, _owners_secret(renewal.secret)):
            if self.decks.shown_source(name, viewer=admin.id) is None:
                raise ProblemError(HTTPStatus.NOT_FOUND)
            raise ProblemError(HTTPStatus.UNPROCESSABLE_ENTITY)
        return Response(status_code=HTTPStatus.NO_CONTENT)

    def renew_webhook_secret(self, name: str) -> WebhookSecretView:
        secret = self.decks.renew_webhook(name)
        if secret is None:
            raise ProblemError(HTTPStatus.NOT_FOUND)
        return WebhookSecretView(webhook_secret=secret)

    def key_draft(self, admin: Annotated[User, Depends(gated_admin)]) -> KeyDraftView:
        draft = self.decks.source_key_draft(owner_id=admin.id)
        return KeyDraftView(id=draft.id, public_key=draft.public_key)

    def check_connection(
        self, probe: SourceProbe, admin: Annotated[User, Depends(gated_admin)]
    ) -> ConnectionCheckView:
        secret, key_draft_id = probe.in_the_owners_form()
        checked = self.decks.check_connection(
            url=probe.url,
            access=probe.access,
            secret=secret,
            owner_id=admin.id,
            key_draft_id=key_draft_id,
        )
        if isinstance(checked, SourceRefusal):
            raise ProblemError(_REFUSED, SourceRefusedProblem(reason=checked))
        return ConnectionCheckView.of(checked)

    def create(
        self,
        request: Request,
        new: NewSource,
        response: Response,
        admin: Annotated[User, Depends(gated_admin)],
    ) -> CreatedSourceView:
        secret, key_draft_id = new.in_the_owners_form()
        added = self.decks.add_source(
            NewSourceDraft(
                name=new.name,
                url=new.url,
                access=new.access,
                secret=secret,
                fingerprint=new.fingerprint,
                key_draft_id=key_draft_id,
            ),
            owner_id=admin.id,
        )
        if isinstance(added, SourceRefusal):
            raise ProblemError(_REFUSED, SourceRefusedProblem(reason=added))
        response.headers["Location"] = _SOURCE.format(name=added.source.name)
        return CreatedSourceView(
            name=added.source.name,
            hook_url=hook_url(request, added.source.name),
            webhook_secret=added.webhook_secret,
        )


def _add_removal_routes(router: APIRouter, data: _SourceData) -> None:
    forbidden = problem_response(HTTPStatus.FORBIDDEN)
    not_found = forbidden | problem_response(HTTPStatus.NOT_FOUND)
    router.add_api_route(_REMOVAL, data.removal, methods=["GET"], responses=not_found)
    router.add_api_route(
        _SOURCE,
        data.remove,
        methods=["DELETE"],
        status_code=HTTPStatus.NO_CONTENT,
        responses=not_found
        | problem_response(HTTPStatus.CONFLICT, SourceRemovalStaleProblem)
        | problem_response(
            HTTPStatus.INTERNAL_SERVER_ERROR, SourceRemovalRefusedProblem
        ),
    )


def source_api_routes(*, decks: Decks) -> APIRouter:
    """The router that answers the Sources data to an admin."""
    data = _SourceData(decks=decks)
    forbidden = problem_response(HTTPStatus.FORBIDDEN)
    not_found = forbidden | problem_response(HTTPStatus.NOT_FOUND)
    router = APIRouter(dependencies=[Depends(gated_admin)])
    router.add_api_route(
        _SOURCES, data.source_list, methods=["GET"], responses=forbidden
    )
    router.add_api_route(
        _SOURCES,
        data.create,
        methods=["POST"],
        status_code=HTTPStatus.CREATED,
        responses=forbidden,
        openapi_extra=problem_beside_validation(SourceRefusedProblem),
    )
    router.add_api_route(_SOURCE, data.source, methods=["GET"], responses=not_found)
    _add_removal_routes(router, data)
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
        _WEBHOOK_SECRET,
        data.renew_webhook_secret,
        methods=["POST"],
        responses=not_found,
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
