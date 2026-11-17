"""With the database URL set the instance serves from Postgres and creates no table."""

from http import HTTPStatus
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from presentator.adapters.postgres.migrate import DatabaseRefusedError
from presentator.host import main
from presentator.host.config import load_settings
from tests.support.lobby import A_BROWSERS_ADDRESS, A_BROWSERS_HEADERS, A_TALK_ADDRESS
from tests.support.log_lines import json_logged
from tests.support.postgres import NO_DATABASE_HERE, database_environment, engine_on
from tests.support.redis import relay_in_front_of


def _point_at(
    environment: pytest.MonkeyPatch, database_url: str, directory: Path
) -> None:
    for name, value in database_environment(database_url, directory).items():
        environment.setenv(name, value)


def test_an_empty_database_is_refused_at_start_and_left_without_a_table(
    environment: pytest.MonkeyPatch, unmigrated_database_url: str, tmp_path: Path
) -> None:
    _point_at(environment, unmigrated_database_url, tmp_path)

    with pytest.raises(DatabaseRefusedError) as refused:
        main.build_instance(load_settings())

    assert refused.value.reason == "no_revision"
    engine = engine_on(unmigrated_database_url)
    try:
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()


def test_a_database_that_does_not_answer_ends_the_start_in_one_failed_line(
    environment: pytest.MonkeyPatch,
    redis_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_at(environment, NO_DATABASE_HERE, tmp_path)
    environment.setenv("PRESENTATOR_REDIS_URL", redis_url)

    with json_logged(capsys) as logged, pytest.raises(SystemExit) as ended:
        main.main([])

    assert ended.value.code == 1
    (connection,) = [
        one
        for one in logged.lines
        if one["event"] == "store_connection.ended" and one["kind"] == "postgres"
    ]
    assert (connection["outcome"], connection["reason"]) == ("failed", "not_answering")
    (failed,) = [one for one in logged.lines if one["event"] == "process.failed"]
    assert (failed["level"], failed["reason"]) == ("error", "not_answering")
    assert failed["error_type"] == psycopg.OperationalError.__name__
    assert "127.0.0.1" not in logged.text


@pytest.mark.parametrize("address", [A_BROWSERS_ADDRESS, A_TALK_ADDRESS])
def test_health_is_ok_while_the_database_may_be_served_and_unavailable_when_not(
    environment: pytest.MonkeyPatch,
    database_url: str,
    redis_url: str,
    tmp_path: Path,
    address: str,
) -> None:
    _point_at(environment, database_url, tmp_path)
    environment.setenv("PRESENTATOR_REDIS_URL", redis_url)
    instance = main.build_instance(load_settings())
    app = instance.lobby if address == A_BROWSERS_ADDRESS else instance.talk
    browser = TestClient(app, base_url=address, headers=A_BROWSERS_HEADERS)
    engine = engine_on(database_url)
    try:
        before = browser.get("/health").status_code
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
        after = browser.get("/health")
    finally:
        engine.dispose()
        instance.close()

    assert before == HTTPStatus.OK
    assert after.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert after.json() == {"status": "unavailable"}


@pytest.mark.parametrize("address", [A_BROWSERS_ADDRESS, A_TALK_ADDRESS])
def test_health_is_unavailable_while_redis_cannot_be_reached(
    environment: pytest.MonkeyPatch,
    database_url: str,
    redis_url: str,
    tmp_path: Path,
    address: str,
) -> None:
    _point_at(environment, database_url, tmp_path)
    with relay_in_front_of(redis_url) as redis:
        environment.setenv("PRESENTATOR_REDIS_URL", redis.url)
        instance = main.build_instance(load_settings())
        app = instance.lobby if address == A_BROWSERS_ADDRESS else instance.talk
        browser = TestClient(app, base_url=address, headers=A_BROWSERS_HEADERS)
        try:
            before = browser.get("/health").status_code
            redis.sever()
            after = browser.get("/health")
        finally:
            instance.close()

    assert before == HTTPStatus.OK
    assert after.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert after.json() == {"status": "unavailable"}
