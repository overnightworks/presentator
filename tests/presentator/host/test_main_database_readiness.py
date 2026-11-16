"""A database is served only at this release's head or newer, and says so when asked."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from sqlalchemy import Engine, text

from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.migrate import (
    database_is_ready,
    migration_history,
    serving_refusal,
)
from tests.presentator.host.support.revision_history import a_longer_history
from tests.support.log_lines import json_logged
from tests.support.postgres import engine_on

_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS = "9999"
_A_PASSWORD = "a-password-that-must-not-leak"  # noqa: S105
_NOTHING_LISTENS_HERE = "postgresql://nobody@127.0.0.1:1/nothing"

type Arrangement = Callable[[Engine, Path], ScriptDirectory]


def _at_head(engine: Engine, directory: Path) -> ScriptDirectory:
    del engine, directory
    return migration_history()


def _newer(engine: Engine, directory: Path) -> ScriptDirectory:
    del directory
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"),
            {"revision": _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS},
        )
    return migration_history()


def _no_revision_row(engine: Engine, directory: Path) -> ScriptDirectory:
    del directory
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
    return migration_history()


def _no_revision_table(engine: Engine, directory: Path) -> ScriptDirectory:
    del directory
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    return migration_history()


def _behind(engine: Engine, directory: Path) -> ScriptDirectory:
    del engine
    return migration_history(a_longer_history(directory))


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    made = engine_on(database_url)
    yield made
    made.dispose()


@pytest.mark.parametrize("arrange", [_at_head, _newer])
def test_a_database_at_head_or_newer_is_served_and_ready(
    arrange: Arrangement, engine: Engine, tmp_path: Path
) -> None:
    history = arrange(engine, tmp_path)

    assert serving_refusal(engine, history) is None
    assert database_is_ready(engine, history)


@pytest.mark.parametrize(
    ("arrange", "reason"),
    [
        (_no_revision_table, "no_revision"),
        (_no_revision_row, "no_revision"),
        (_behind, "behind_this_release"),
    ],
)
def test_an_empty_or_older_database_is_refused_and_not_ready(
    arrange: Arrangement,
    reason: str,
    engine: Engine,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    history = arrange(engine, tmp_path)

    refusal = serving_refusal(engine, history)
    with json_logged(capsys) as logged:
        ready = database_is_ready(engine, history)

    assert refusal is not None
    assert refusal.reason == reason
    assert not ready
    (line,) = [
        one for one in logged.lines if one["event"] == "database_readiness.refused"
    ]
    assert line["reason"] == reason


def test_a_database_that_does_not_answer_is_not_ready_and_the_log_names_no_secret(
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = create_database_engine(_NOTHING_LISTENS_HERE, SecretStr(_A_PASSWORD))
    try:
        with json_logged(capsys) as logged:
            ready = database_is_ready(engine, migration_history())
    finally:
        engine.dispose()

    assert not ready
    (line,) = [
        one for one in logged.lines if one["event"] == "database_readiness.refused"
    ]
    assert line["reason"] == "not_answering"
    assert _A_PASSWORD not in logged.text
    assert "nobody" not in logged.text
