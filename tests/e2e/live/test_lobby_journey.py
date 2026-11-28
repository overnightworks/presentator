"""The lobby journey: the lobby page, the session and the deck list, round on round."""

# ruff: noqa: T201 - the journey's one output is its round and failure counts

import time
from collections import Counter

import pytest
from playwright.sync_api import BrowserContext, Error, Response

pytestmark = pytest.mark.live

_STEP_PAGE = "page"
_STEP_FILE = "file"
_STEP_SESSION = "session"
_STEP_DECKS = "decks"
_UNANSWERED = 0
_FIRST_ERROR_STATUS = 400


def _count_file(failures: Counter[str], response: Response) -> None:
    if response.status >= _FIRST_ERROR_STATUS:
        failures.update([f"{_STEP_FILE}={response.status}"])


def _round(context: BrowserContext, origin: str) -> Counter[str]:
    """Run one round and count its failures as `<step>=<status>`.

    A status of 0 is a request that got no answer. Nothing else is recorded, so
    the output carries no cookie, URL or page content.
    """
    failures: Counter[str] = Counter()
    page = context.new_page()
    page.on("response", lambda response: _count_file(failures, response))
    page.on("requestfailed", lambda _request: failures.update([f"{_STEP_FILE}=0"]))
    try:
        answer = page.goto(origin + "/", wait_until="networkidle")
    except Error:
        failures.update([f"{_STEP_PAGE}={_UNANSWERED}"])
        return failures
    finally:
        page_url = page.url
        page.close()
    if answer is None or not answer.ok or "/login" in page_url:
        failures.update(
            [f"{_STEP_PAGE}={_UNANSWERED if answer is None else answer.status}"]
        )
    for step, path in ((_STEP_SESSION, "/api/session"), (_STEP_DECKS, "/api/decks")):
        try:
            reply = context.request.get(origin + path)
        except Error:
            failures.update([f"{step}={_UNANSWERED}"])
            continue
        if not reply.ok:
            failures.update([f"{step}={reply.status}"])
        elif step == _STEP_SESSION and reply.json()["person"] is None:
            failures.update([f"{step}=signed-out"])
    return failures


def test_lobby_holds_round_after_round(
    context: BrowserContext, origin: str, loop_span: int
) -> None:
    deadline = time.monotonic() + loop_span
    rounds = 0
    failures: Counter[str] = Counter()
    while True:
        rounds += 1
        failures.update(_round(context, origin))
        if failures or time.monotonic() >= deadline:
            break
    steps = ", ".join(f"{key} x{count}" for key, count in sorted(failures.items()))
    print(f"rounds={rounds} failed={1 if failures else 0} {steps}".rstrip())
    if failures:
        pytest.fail("a round failed", pytrace=False)
