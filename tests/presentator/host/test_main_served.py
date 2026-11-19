"""What the composition root hands the server it serves through."""

from http import HTTPStatus

import pytest
import uvicorn
from fastapi.testclient import TestClient
from logline import RequestLineMiddleware

from presentator.api.health import Stopping
from presentator.host import main
from presentator.host.lifecycle import StopLine
from tests.support.lobby import A_BROWSERS_ADDRESS, A_BROWSERS_HEADERS

pytestmark = pytest.mark.usefixtures("redis_environment", "served_database")


type ServedByMain = tuple[
    list[RequestLineMiddleware], list[uvicorn.Config], list[Stopping]
]


@pytest.fixture
def served_by_main(environment: pytest.MonkeyPatch, json_logging: None) -> ServedByMain:
    """What the composition root hands the server, recorded instead of served."""
    del json_logging
    served: list[RequestLineMiddleware] = []
    configs: list[uvicorn.Config] = []
    stoppings: list[Stopping] = []

    class ServerThatOnlyRecordsWhatItGot:
        def __init__(
            self, config: uvicorn.Config, stopping: Stopping, stop_line: StopLine
        ) -> None:
            del stop_line
            assert isinstance(config.app, RequestLineMiddleware)
            configs.append(config)
            stoppings.append(stopping)
            self.app = config.app

        async def serve(self) -> None:
            served.append(self.app)

    environment.setattr(main, "StoppingServer", ServerThatOnlyRecordsWhatItGot)

    main.main([])

    return served, configs, stoppings


def test_the_composition_root_serves_a_lobby_that_answers_the_login(
    served_by_main: ServedByMain,
) -> None:
    served, _, _ = served_by_main

    assert len(served) == 1
    browser = TestClient(
        served[0], base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    )
    assert browser.get("/login").is_success


def test_the_composition_root_hands_the_server_the_stopping_its_app_answers_by(
    served_by_main: ServedByMain,
) -> None:
    served, _, stoppings = served_by_main
    browser = TestClient(
        served[0], base_url=A_BROWSERS_ADDRESS, headers=A_BROWSERS_HEADERS
    )

    stoppings[0].begin()

    assert browser.get("/health").status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_the_composition_root_serves_websockets_through_wsproto(
    served_by_main: ServedByMain,
) -> None:
    _, configs, _ = served_by_main

    assert configs[0].ws == "wsproto"
