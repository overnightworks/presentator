"""SI14: each host counts requests per address, so one client cannot spend another's.

The backend counts by `time.monotonic`, so each case spends its budget for real
and the sizes are read from the policies the hosts run.
"""

from dataclasses import replace
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from webauth.policies import RateLimitBudget

from presentator.api.auth import (
    BACKCHANNEL_LOGOUT_PATH,
    DEFAULT_BUDGET,
    LOGOUT_PATH,
    SIGN_IN_BUDGET,
    SIGN_IN_CALLBACK_PATH,
    START_SIGN_IN_PATH,
    TALK_FILES_BUDGET,
)
from presentator.api.health import HEALTH_PATH
from presentator.api.session_api import SESSION_PATH
from presentator.contracts.decks import DECK_PATH
from presentator.host.main import web_auth_config_for
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_TALK_ADDRESS,
    SigningIn,
    a_crowd,
    a_lobby_app,
    an_installed_auth,
)
from tests.support.talk_support import Hosts, two_hosts

_AN_OTHER_PATH = "/somewhere-else"
_A_DECK_FILE = f"{DECK_PATH}/a-deck/index.html"
_FORWARDED_FOR = "x-forwarded-for"
_PROXY_NETWORK = "10.0.0.0/8"
_A_PROXY = "10.0.0.1"


def spend(client: TestClient, path: str, budget: RateLimitBudget) -> None:
    """Make the requests the budget grants one address; the next one is refused."""
    for _ in range(budget.max_requests):
        assert client.get(path).status_code != HTTPStatus.TOO_MANY_REQUESTS


@pytest.fixture
def hosts(tmp_path: Path) -> Hosts:
    return two_hosts(tmp_path)


@pytest.mark.parametrize(
    ("address", "spent_on", "refused_at"),
    [
        (A_BROWSERS_ADDRESS, START_SIGN_IN_PATH, START_SIGN_IN_PATH),
        (A_BROWSERS_ADDRESS, START_SIGN_IN_PATH, SIGN_IN_CALLBACK_PATH),
        (A_BROWSERS_ADDRESS, START_SIGN_IN_PATH, LOGOUT_PATH),
        (A_TALK_ADDRESS, START_SIGN_IN_PATH, START_SIGN_IN_PATH),
        (A_TALK_ADDRESS, START_SIGN_IN_PATH, SIGN_IN_CALLBACK_PATH),
    ],
)
def test_the_sign_in_paths_of_a_host_share_one_budget_per_address(
    hosts: Hosts, address: str, spent_on: str, refused_at: str
) -> None:
    client = hosts.browser(address)
    spend(client, spent_on, SIGN_IN_BUDGET)

    refused = client.get(refused_at)

    assert refused.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "retry-after" in refused.headers


def test_a_spent_sign_in_budget_leaves_the_api_of_a_signed_in_person_open(
    hosts: Hosts,
) -> None:
    spend(hosts.browser(A_BROWSERS_ADDRESS), START_SIGN_IN_PATH, SIGN_IN_BUDGET)

    answer = hosts.signed_in_at(A_BROWSERS_ADDRESS).get(SESSION_PATH)

    assert answer.status_code != HTTPStatus.TOO_MANY_REQUESTS


def test_any_other_path_of_the_lobby_is_refused_past_the_default_budget(
    hosts: Hosts,
) -> None:
    client = hosts.browser(A_BROWSERS_ADDRESS)
    spend(client, _AN_OTHER_PATH, DEFAULT_BUDGET)

    assert client.get(_AN_OTHER_PATH).status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.parametrize(
    ("address", "exempt_request"),
    [
        (A_BROWSERS_ADDRESS, ("GET", HEALTH_PATH)),
        (A_BROWSERS_ADDRESS, ("POST", BACKCHANNEL_LOGOUT_PATH)),
        (A_TALK_ADDRESS, ("GET", HEALTH_PATH)),
    ],
)
def test_health_and_the_back_channel_logout_are_never_refused_by_a_budget(
    hosts: Hosts, address: str, exempt_request: tuple[str, str]
) -> None:
    client = hosts.browser(address)
    spend(client, _AN_OTHER_PATH, DEFAULT_BUDGET)
    method, path = exempt_request

    assert client.request(method, path).status_code != HTTPStatus.TOO_MANY_REQUESTS


def test_the_talk_host_counts_apart_from_the_lobby(hosts: Hosts) -> None:
    spend(hosts.browser(A_BROWSERS_ADDRESS), _AN_OTHER_PATH, DEFAULT_BUDGET)

    answer = hosts.browser(A_TALK_ADDRESS).get(_AN_OTHER_PATH)

    assert answer.status_code != HTTPStatus.TOO_MANY_REQUESTS


def test_deck_files_spend_the_talk_files_budget_not_the_default_one(
    hosts: Hosts,
) -> None:
    client = hosts.browser(A_TALK_ADDRESS)
    spend(client, _A_DECK_FILE, DEFAULT_BUDGET)

    assert client.get(_A_DECK_FILE).status_code != HTTPStatus.TOO_MANY_REQUESTS


def test_deck_files_are_refused_past_the_talk_files_budget(hosts: Hosts) -> None:
    client = hosts.browser(A_TALK_ADDRESS)
    spend(client, _A_DECK_FILE, TALK_FILES_BUDGET)

    assert client.get(_A_DECK_FILE).status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_a_path_that_only_starts_like_the_deck_directory_spends_the_default_budget(
    hosts: Hosts,
) -> None:
    client = hosts.browser(A_TALK_ADDRESS)
    spend(client, f"{DECK_PATH}X", DEFAULT_BUDGET)

    assert client.get(f"{DECK_PATH}X").status_code == HTTPStatus.TOO_MANY_REQUESTS


def test_behind_a_trusted_proxy_each_forwarded_address_has_its_own_budget() -> None:
    people = a_crowd()
    auth = an_installed_auth(people.clock)
    behind_a_proxy = replace(
        auth,
        config=web_auth_config_for(
            auth.config.session_secret,
            trusted_proxies=_PROXY_NETWORK,
            origin=A_BROWSERS_ADDRESS,
        ),
    )
    app, _ = a_lobby_app(
        people=people,
        signing_in=SigningIn(sessions=people.sessions, auth=behind_a_proxy),
    )
    through_the_proxy = TestClient(
        app,
        base_url=A_BROWSERS_ADDRESS,
        client=(_A_PROXY, 1),
        follow_redirects=False,
    )

    def forwarded(address: str) -> dict[str, str]:
        return {_FORWARDED_FOR: address}

    for _ in range(DEFAULT_BUDGET.max_requests):
        through_the_proxy.get(_AN_OTHER_PATH, headers=forwarded("203.0.113.1"))
    first = through_the_proxy.get(_AN_OTHER_PATH, headers=forwarded("203.0.113.1"))
    second = through_the_proxy.get(_AN_OTHER_PATH, headers=forwarded("203.0.113.2"))

    assert first.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert second.status_code != HTTPStatus.TOO_MANY_REQUESTS
