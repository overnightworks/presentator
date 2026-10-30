"""RFC 9457 problem details: the one shape presentator's own JSON errors take.

A route raises `ProblemError`; the handler answers it. For `about:blank` the
title is the HTTP reason phrase, so presentator words nothing and a client
words the status from its own catalog. A problem with members of its own is a
`ProblemDetails` subclass that fixes its type and carries those members.
webauth's refusals and the framework's own answers keep their shape. The
framework's validation answer keeps its status, media type and shape too, but
each error is reduced to `loc`, `msg`
and `type`: pydantic's `input` and `ctx` would repeat what the request sent.
Pydantic's own `msg` and `loc` can still carry a value for a discriminated-union
tag, a `dict` key and UUID parsing, so a request model that takes a secret must
not use those.
"""

from http import HTTPStatus
from typing import Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from pydantic.config import JsonDict
from starlette.responses import Response

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
_ABOUT_BLANK: Final = "about:blank"
_IDENTITY: Final = ("type", "title", "status")


def _describe_wire(schema: JsonDict) -> None:
    """Every problem answers its `type`, `title` and `status`, so the spec says so.

    `title` and `status` carry placeholders until `ProblemError` sets them, so
    neither shows a default in the schema.
    """
    required = schema.setdefault("required", [])
    if isinstance(required, list):
        required.extend(each for each in _IDENTITY if each not in required)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for each in ("title", "status"):
            member = properties.get(each)
            if isinstance(member, dict):
                member.pop("default", None)


class ProblemDetails(BaseModel):
    """The body of a problem; fields that are `None` stay out of the wire."""

    model_config = ConfigDict(frozen=True, json_schema_extra=_describe_wire)

    type: str = _ABOUT_BLANK
    title: str = Field(default="", init=False)
    status: int = Field(default=0, init=False)
    detail: str | None = None


class ProblemError(Exception):
    """Raised by a route to answer a status as a problem, `about:blank` by default.

    The handler is the one owner of the body's `title` and `status`: whatever
    the problem carried, they are the raised status's phrase and value.
    """

    def __init__(
        self, status: HTTPStatus, problem: ProblemDetails | None = None
    ) -> None:
        """Remember the status, and the problem that answers it."""
        super().__init__(status.phrase)
        self.status = status
        self.problem = (problem or ProblemDetails()).model_copy(
            update={"title": status.phrase, "status": status.value}
        )


def problem_response(
    status: HTTPStatus, problem: type[ProblemDetails] = ProblemDetails
) -> dict[int | str, dict[str, object]]:
    """The route's OpenAPI `responses` entry, so a generated client types it.

    A typed problem writes every member's schema inline, because a `$ref` would
    dangle once the schema sits in the OpenAPI document beside the framework's.
    """
    return {
        status.value: {
            "content": {PROBLEM_MEDIA_TYPE: {"schema": problem.model_json_schema()}},
        }
    }


def problem_beside_validation(
    problem: type[ProblemDetails] = ProblemDetails,
) -> dict[str, object]:
    """A write's 422 spec: FastAPI's own validation answer plus our problem.

    FastAPI generates the validation entry (and its components) only while the
    route declares no 422 itself, so the problem's media type joins it afterwards
    through the route's `openapi_extra`.
    """
    status = HTTPStatus.UNPROCESSABLE_ENTITY
    return {
        "responses": {
            str(status.value): problem_response(status, problem)[status.value]
        }
    }


def _answer(_request: Request, error: Exception) -> Response:
    problem_error = cast("ProblemError", error)
    return Response(
        problem_error.problem.model_dump_json(exclude_none=True),
        status_code=problem_error.status.value,
        media_type=PROBLEM_MEDIA_TYPE,
    )


class ValidationFault(BaseModel):
    """One validation error as the wire carries it: nothing the request sent."""

    model_config = ConfigDict(frozen=True)

    loc: tuple[str | int, ...]
    msg: str
    type: str


class ValidationFaults(BaseModel):
    """The body of a validation answer, in the framework's `detail` shape."""

    model_config = ConfigDict(frozen=True)

    detail: tuple[ValidationFault, ...]


def _answer_validation(_request: Request, error: Exception) -> Response:
    invalid = cast("RequestValidationError", error)
    faults = ValidationFaults(
        detail=tuple(
            ValidationFault(loc=fault["loc"], msg=fault["msg"], type=fault["type"])
            for fault in invalid.errors()
        )
    )
    return Response(
        faults.model_dump_json(),
        status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
        media_type="application/json",
    )


def install_error_answers(app: FastAPI) -> None:
    """Answer every `ProblemError` as problem details, validation without input."""
    app.add_exception_handler(ProblemError, _answer)
    app.add_exception_handler(RequestValidationError, _answer_validation)
