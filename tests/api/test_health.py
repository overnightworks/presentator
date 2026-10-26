"""Both hosts answer `GET /health` to anyone, touching no session."""

from http import HTTPStatus
from pathlib import Path

import pytest

from tests.api.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_TALK_ADDRESS,
    a_browser_at,
    a_crowd,
    a_lobby_app,
)
from tests.api.talk_support import two_hosts

_HOSTS = [A_BROWSERS_ADDRESS, A_TALK_ADDRESS]


@pytest.mark.parametrize("address", _HOSTS)
def test_a_signed_out_reader_gets_the_fixed_ok_body_at_each_host(
    tmp_path: Path, address: str
) -> None:
    """#513 acceptance 1, 2 and 3: signed out, each host answers 200 ok with the fixed JSON body."""
    answer = two_hosts(tmp_path).browser(address).get("/health")

    assert answer.status_code == HTTPStatus.OK
    assert answer.headers["content-type"] == "application/json"
    assert answer.json() == {"status": "ok"}


@pytest.mark.parametrize("address", _HOSTS)
def test_the_answer_sets_no_cookie(tmp_path: Path, address: str) -> None:
    """#513 acceptance 4: the answer sets no cookie."""
    answer = two_hosts(tmp_path).browser(address).get("/health")

    assert "set-cookie" not in answer.headers


@pytest.mark.parametrize("address", _HOSTS)
@pytest.mark.parametrize("method", ["HEAD", "POST"])
def test_only_a_get_is_answered(tmp_path: Path, address: str, method: str) -> None:
    """#513 acceptance 6: only GET is answered."""
    client = two_hosts(tmp_path).browser(address)

    answer = client.request(
        method, "/health", headers={"sec-fetch-site": "same-origin"}
    )

    assert answer.status_code == HTTPStatus.METHOD_NOT_ALLOWED


@pytest.mark.parametrize("address", _HOSTS)
def test_an_address_below_health_is_still_gated(tmp_path: Path, address: str) -> None:
    """#513 Done when: no web route other than /health became public."""
    answer = two_hosts(tmp_path).browser(address).get("/health/more")

    assert answer.status_code == HTTPStatus.FOUND
    assert answer.headers["location"].startswith("/login/oidc?next=")


def test_an_unknown_host_gets_not_found(tmp_path: Path) -> None:
    """#513 acceptance 7: an unknown host still gets 404."""
    answer = two_hosts(tmp_path).browser("https://other.example").get("/health")

    assert answer.status_code == HTTPStatus.NOT_FOUND


def test_the_lobby_reads_no_session_store_to_answer() -> None:
    """#513 acceptance 5: the route reads no session store."""
    people = a_crowd()
    app, _clock = a_lobby_app(people=people)
    lobby = a_browser_at(
        app, people=people, base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    )
    lobby.sign_in()

    answer = lobby.client.get("/health")

    assert answer.status_code == HTTPStatus.OK
    assert (people.sessions.loads, people.sessions.touches) == (0, 0)
