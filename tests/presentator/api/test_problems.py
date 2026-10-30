"""A problem's title and status belong to the raised status, never to the caller."""

from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from presentator.api.problems import (
    PROBLEM_MEDIA_TYPE,
    ProblemDetails,
    ProblemError,
    install_error_answers,
)


class _Typed(ProblemDetails):
    type: str = "tag:presentator.test,2026:typed"
    reason: str


def _answer_of(error: ProblemError) -> dict[str, object]:
    app = FastAPI()
    install_error_answers(app)

    @app.get("/raises")
    def raises() -> None:
        raise error

    answer = TestClient(app).get("/raises")
    assert answer.headers["content-type"] == PROBLEM_MEDIA_TYPE
    body: dict[str, object] = answer.json()
    assert body["status"] == answer.status_code
    return body


@pytest.mark.parametrize(
    "problem",
    [
        ProblemDetails(),
        _Typed(reason="why"),
        _Typed.model_construct(title="Our own words", status=200, reason="why"),
    ],
    ids=["plain", "typed", "carrying its own title and status"],
)
def test_the_title_is_the_raised_statuss_phrase_whatever_the_problem_carried(
    problem: ProblemDetails,
) -> None:
    body = _answer_of(ProblemError(HTTPStatus.CONFLICT, problem))

    assert body["title"] == HTTPStatus.CONFLICT.phrase
    assert body["status"] == HTTPStatus.CONFLICT


def test_a_member_the_problem_does_not_declare_is_refused() -> None:
    with pytest.raises(ValueError, match="Extra inputs"):
        _Typed.model_validate({"reason": "why", "reasn": "typo"})
