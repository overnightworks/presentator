"""RFC 9457 problem details: the one shape presentator's own JSON errors take.

A route raises `ProblemError`; the handler answers it. For `about:blank` the
title is the HTTP reason phrase, so presentator words nothing and a client
words the status from its own catalog. webauth's refusals and the framework's
own answers keep their shape. The framework's validation answer keeps its
status, media type and shape too, but each error is reduced to `loc`, `msg`
and `type`: pydantic's `input` and `ctx` would repeat what the request sent.
"""

from http import HTTPStatus
from typing import Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

PROBLEM_MEDIA_TYPE: Final = "application/problem+json"
_ABOUT_BLANK: Final = "about:blank"


class ProblemDetails(BaseModel):
    """The body of a problem; fields that are `None` stay out of the wire."""

    model_config = ConfigDict(frozen=True)

    type: str = _ABOUT_BLANK
    title: str
    status: int
    detail: str | None = None


class ProblemError(Exception):
    """Raised by a route to answer a status as a problem of type `about:blank`."""

    def __init__(self, status: HTTPStatus) -> None:
        """Remember the status the handler answers with."""
        super().__init__(status.phrase)
        self.status = status


def problem_response(status: HTTPStatus) -> dict[int | str, dict[str, object]]:
    """The route's OpenAPI `responses` entry, so a generated client types it."""
    return {
        status.value: {
            "content": {
                PROBLEM_MEDIA_TYPE: {"schema": ProblemDetails.model_json_schema()}
            },
        }
    }


def _answer(_request: Request, error: Exception) -> Response:
    problem_error = cast("ProblemError", error)
    status = problem_error.status
    problem = ProblemDetails(title=status.phrase, status=status.value)
    return Response(
        problem.model_dump_json(exclude_none=True),
        status_code=status.value,
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
