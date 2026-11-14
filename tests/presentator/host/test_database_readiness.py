"""A database is served only at this release's head or newer, and says so when asked."""

import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from presentator.adapters.postgres import migrate as migrations
from presentator.adapters.postgres.engine import create_database_engine
from presentator.adapters.postgres.migrate import database_is_ready, revision_refusal
from tests.presentator.host.support.revision_history import a_longer_history
from tests.support.postgres import engine_on

_A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS = "9999"
_A_PASSWORD = "a-password-that-must-not-leak"  # noqa: S105
_NOTHING_LISTENS_HERE = "postgresql://nobody@127.0.0.1:1/nothing"

type Arrangement = Callable[[Engine, Path, pytest.MonkeyPatch], None]


def _at_head(engine: Engine, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del engine, directory, monkeypatch


def _newer(engine: Engine, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del directory, monkeypatch
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version VALUES (:revision)"),
            {"revision": _A_REVISION_ONLY_A_NEWER_RELEASE_KNOWS},
        )


def _no_revision_row(
    engine: Engine, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del directory, monkeypatch
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM alembic_version"))


def _no_revision_table(
    engine: Engine, directory: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del directory, monkeypatch
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))


def _behind(engine: Engine, directory: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    del engine
    monkeypatch.setattr(migrations, "MIGRATIONS_DIRECTORY", a_longer_history(directory))


@pytest.fixture
def engine(database_url: str) -> Iterator[Engine]:
    made = engine_on(database_url)
    yield made
    made.dispose()


@pytest.mark.parametrize("arrange", [_at_head, _newer])
def test_a_database_at_head_or_newer_is_served_and_ready(
    arrange: Arrangement,
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrange(engine, tmp_path, monkeypatch)

    assert revision_refusal(engine) is None
    assert database_is_ready(engine)


@pytest.mark.parametrize("arrange", [_no_revision_table, _no_revision_row, _behind])
def test_an_empty_or_older_database_is_refused_and_not_ready(
    arrange: Arrangement,
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    arrange(engine, tmp_path, monkeypatch)

    assert revision_refusal(engine)
    assert not database_is_ready(engine)


def test_a_database_that_does_not_answer_is_not_ready_and_the_log_names_no_secret(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_database_engine(_NOTHING_LISTENS_HERE, SecretStr(_A_PASSWORD))
    try:
        with caplog.at_level(logging.WARNING):
            ready = database_is_ready(engine)
    finally:
        engine.dispose()

    assert not ready
    assert _A_PASSWORD not in caplog.text
    assert "nobody" not in caplog.text
