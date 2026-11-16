"""The web writes its start, its stores, its readiness and its stop as JSON lines."""

import signal
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr

from presentator.adapters.shared_redis import SharedRedis
from presentator.host.store_connection import reach_redis
from tests.presentator.host.support.app_processes import App, serving_apps
from tests.support.log_lines import LogLine, json_lines_among, json_logged
from tests.support.redis import NOTHING_LISTENS_HERE

_LIFECYCLE = {
    "process.started",
    "store_connection.ended",
    "process.ready",
    "process.stopped",
}
_A_PASSWORD = "a-redis-password-that-must-not-leak"  # noqa: S105


@pytest.fixture
def app(
    environment: pytest.MonkeyPatch, database_url: str, tmp_path: Path
) -> Iterator[App]:
    del environment
    with serving_apps(database_url, tmp_path) as (first, _second):
        yield first


def _lifecycle(output: str) -> list[LogLine]:
    return [one for one in json_lines_among(output) if one["event"] in _LIFECYCLE]


def test_a_served_process_writes_start_one_line_per_store_ready_and_stop(
    app: App,
) -> None:
    app.process.send_signal(signal.SIGTERM)
    lines = _lifecycle(app.output_after_exit())

    assert [one["event"] for one in lines[:2]] == [
        "process.started",
        "store_connection.ended",
    ]
    assert [one["event"] for one in lines[-2:]] == ["process.ready", "process.stopped"]
    stores = {
        one["kind"]: one for one in lines if one["event"] == "store_connection.ended"
    }
    assert set(stores) == {"postgres", "redis"}
    for line in stores.values():
        assert line["outcome"] == "ok"
        assert isinstance(line["duration_ms"], int | float)


def test_no_line_of_a_served_process_carries_a_store_address(app: App) -> None:
    app.process.send_signal(signal.SIGTERM)
    output = app.output_after_exit()

    assert "redis://" not in output
    assert "postgresql://" not in output


def test_a_redis_that_does_not_answer_writes_one_failed_line_and_names_no_address(
    capsys: pytest.CaptureFixture[str],
) -> None:
    redis = SharedRedis(
        NOTHING_LISTENS_HERE.replace("redis://", f"redis://:{_A_PASSWORD}@"),
        SecretStr(_A_PASSWORD),
    )

    with json_logged(capsys) as logged:
        reach_redis(redis)
    redis.close()

    (line,) = logged.lines
    assert (line["kind"], line["outcome"]) == ("redis", "failed")
    assert (line["reason"], line["level"]) == ("not_answering", "warning")
    assert _A_PASSWORD not in logged.text
    assert "127.0.0.1" not in logged.text
