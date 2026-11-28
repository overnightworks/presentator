"""What the live journeys are given: an origin, a person's cookies and a span."""

import json
import os
from pathlib import Path

import pytest

DEFAULT_LOOP_SPAN_SECONDS = 120
USAGE_ERROR = pytest.ExitCode.USAGE_ERROR


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("live", "live journeys")
    group.addoption("--origin", help="the origin the journeys read, without a slash")
    group.addoption(
        "--storage-state",
        help="a Playwright storage state holding the person's cookies",
    )
    group.addoption(
        "--loop-span",
        type=int,
        default=DEFAULT_LOOP_SPAN_SECONDS,
        help="seconds a journey keeps starting rounds",
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """End a run that selects a journey but has no readable storage state or origin.

    This runs before any round; a run that deselects the journeys, CI's included,
    has no live item and never checks.
    """
    if not any(item.get_closest_marker("live") for item in session.items):
        return
    config = session.config
    storage_state = config.getoption("storage_state")
    if not (
        storage_state
        and Path(storage_state).is_file()
        and os.access(storage_state, os.R_OK)
    ):
        pytest.exit("--storage-state names no readable file", returncode=USAGE_ERROR)
    try:
        json.loads(Path(storage_state).read_text())
    except ValueError:
        pytest.exit("--storage-state holds no JSON", returncode=USAGE_ERROR)
    if not config.getoption("origin"):
        pytest.exit("--origin is missing", returncode=USAGE_ERROR)


@pytest.fixture(scope="session")
def origin(pytestconfig: pytest.Config) -> str:
    return str(pytestconfig.getoption("origin")).rstrip("/")


@pytest.fixture(scope="session")
def loop_span(pytestconfig: pytest.Config) -> int:
    return int(str(pytestconfig.getoption("loop_span")))


@pytest.fixture(scope="session")
def browser_context_args(
    browser_context_args: dict[str, object], pytestconfig: pytest.Config
) -> dict[str, object]:
    return {
        **browser_context_args,
        "storage_state": str(pytestconfig.getoption("storage_state")),
    }
