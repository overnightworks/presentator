"""A stopping app finishes its answers and ends hearings with 1012; another hears on."""

import signal
import threading
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import Engine

from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS,
    with_the_toolchain,
)
from tests.presentator.host.support.app_processes import (
    TALK_HOST,
    App,
    Browser,
    serving_apps,
)
from tests.presentator.host.support.app_requests import (
    AT,
    added,
    admin_of,
    fetched,
    scalar,
)
from tests.presentator.host.support.copresenter_services import (
    HeldGateway,
    a_gateway_and_a_speech_service,
    a_hearing,
)
from tests.support.git_remote import EXAMPLE_SLUG, GitRemote
from tests.support.lobby import A_TALK_ADDRESS, AN_API_GROUP
from tests.support.postgres import engine_on
from tests.support.sign_in_support import ADMIN

_SERVICE_RESTART = 1012
_SECONDS = 60
_BUILT_DECKS = "SELECT count(*) FROM decks WHERE built_commit IS NOT NULL"


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    each = engine_on(database_url)
    try:
        yield each
    finally:
        each.dispose()


def _wait_until_built(engine: Engine) -> None:
    deadline = threading.Event()
    for _ in range(_SECONDS * 10):
        if scalar(engine, _BUILT_DECKS):
            return
        deadline.wait(0.1)
    message = "the deck never built"
    raise AssertionError(message)


def _ask(app: App, person: Browser) -> httpx2.Response:
    return httpx2.post(
        f"http://127.0.0.1:{app.port}/copresenter/ask",
        headers={
            "host": TALK_HOST,
            "origin": A_TALK_ADDRESS,
            "sec-fetch-site": "same-origin",
            "cookie": person.cookie_header,
        },
        json={"said": "Wie geht es?", "slide": 1, "deck": EXAMPLE_SLUG},
        timeout=_SECONDS,
    )


def test_a_stopping_app_drains_and_the_other_app_hears_on(
    environment: pytest.MonkeyPatch,
    database_url: str,
    tmp_path: Path,
    engine: Engine,
    remote: GitRemote,
) -> None:
    with_the_toolchain(environment, tmp_path, A_TOOLCHAIN_THAT_BUILDS)
    remote.commit_example_deck(at=AT)
    gateway = HeldGateway()
    with (
        a_gateway_and_a_speech_service(gateway) as services,
        serving_apps(database_url, tmp_path, services) as (stopping, other),
    ):
        admin = admin_of(stopping)
        added(admin, stopping, "talks", remote)
        _wait_until_built(engine)
        fetched(admin, other, "talks")
        person = Browser(host=TALK_HOST)
        person.sign_in(stopping, "admin", groups=[*ADMIN, AN_API_GROUP])
        hearing_at = {"host": TALK_HOST, "origin": A_TALK_ADDRESS}

        with a_hearing(
            stopping.port, **hearing_at, cookie=person.cookie_header
        ) as hearing:
            assert hearing.hear_after_saying() != ""
            answers: list[httpx2.Response] = []
            asking = threading.Thread(
                target=lambda: answers.append(_ask(stopping, person))
            )
            asking.start()
            assert gateway.started.wait(_SECONDS)

            stopping.process.send_signal(signal.SIGTERM)

            assert hearing.close_code() == _SERVICE_RESTART
            assert stopping.process.poll() is None
            gateway.release.set()
            asking.join(_SECONDS)
            # uvicorn raises the signal again once it has stopped, so a clean
            # stop reads as the signal's own end.
            assert stopping.process.wait(timeout=_SECONDS) in {0, -signal.SIGTERM}

        assert answers[0].status_code == HTTPStatus.OK
        assert "event: done" in answers[0].text
        with a_hearing(other.port, **hearing_at, cookie=person.cookie_header) as again:
            assert again.hear_after_saying() != ""
