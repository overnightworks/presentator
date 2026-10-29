"""RFC 9457 problem details: the one shape presentator's own JSON errors take.

A route raises `ProblemError`; the handler answers it. For `about:blank` the
title is the HTTP reason phrase, so presentator words nothing and a client
words the status from its own catalog. webauth's refusals and the framework's
own answers keep their shape.
"""

from http import HTTPStatus
from typing import Final, cast

from fastapi import FastAPI, Request
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


def install_problem_handler(app: FastAPI) -> None:
    """Answer every `ProblemError` a route raises as problem details."""
    app.add_exception_handler(ProblemError, _answer)
