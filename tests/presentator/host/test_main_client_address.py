"""The host's one writer of the client address sits directly inside the request line."""

from datetime import timedelta

from fastapi import FastAPI
from logline import RequestLineMiddleware
from starlette.routing import Router
from webauth.middleware import ClientAddressMiddleware

from presentator.host.main import Instance, served_by_host
from presentator.host.polling import ChoreCode, Schedule
from tests.support.lobby import a_settings


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
