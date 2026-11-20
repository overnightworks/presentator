"""The host's one writer of the client address sits directly inside the request line."""

import logging
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from logline import RequestLineMiddleware
from starlette.routing import Router
from webauth.middleware import ClientAddressMiddleware

from presentator.host.main import Instance, served_by_host
from presentator.host.polling import ChoreCode, Schedule
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    a_crowd,
    a_lobby_app,
    a_settings,
    a_talk_app,
)
from tests.support.log_lines import request_lines

_THE_PROXY = "10.1.2.3"
_THE_CLIENT = "203.0.113.7"


def _a_schedule(chore_code: ChoreCode) -> Schedule:
    return Schedule(chore=lambda: None, chore_code=chore_code, interval=timedelta(0))


def test_the_served_app_is_the_request_line_around_the_writer_around_the_router() -> (
    None
):
    instance = Instance(
        lobby=FastAPI(),
        talk=FastAPI(),
        poller=_a_schedule(ChoreCode.SOURCE_POLL),
        session_sweep=_a_schedule(ChoreCode.SESSION_SWEEP),
    )

    served = served_by_host(instance, a_settings())

    assert isinstance(served, RequestLineMiddleware)
    # No request shows the order, and webauth cannot see a writer inside another layer.
    writer = served._app  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
    assert isinstance(writer, ClientAddressMiddleware)
    assert isinstance(writer._app, Router)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]


def test_apps_built_for_the_host_leave_the_proxy_policy_to_the_routers_writer(
    json_logging: None, caplog: pytest.LogCaptureFixture
) -> None:
    del json_logging
    caplog.set_level(logging.INFO)
    people = a_crowd()
    lobby, _ = a_lobby_app(people=people, trusted_proxies=None)
    instance = Instance(
        lobby=lobby,
        talk=a_talk_app(people=people, trusted_proxies=None),
        poller=_a_schedule(ChoreCode.SOURCE_POLL),
        session_sweep=_a_schedule(ChoreCode.SESSION_SWEEP),
    )
    served = served_by_host(
        instance, a_settings().model_copy(update={"trusted_proxies": _THE_PROXY})
    )

    TestClient(served, base_url=A_BROWSERS_ADDRESS, client=(_THE_PROXY, 50000)).get(
        "/", headers={"x-forwarded-for": _THE_CLIENT}
    )

    assert [line["ip"] for line in request_lines(caplog)] == [_THE_CLIENT]
