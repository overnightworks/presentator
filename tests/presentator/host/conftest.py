"""Fixtures the worker's host tests share."""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.presentator.host.support.sealed_snapshot import stage_sealed_root
from tests.support.worker_support import CLIENTS


@pytest.fixture
def sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return stage_sealed_root(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def logging_undone_after_main(json_logging: None) -> Iterator[None]:
    """Every `main()` a host test runs configures the process's logging.

    `main()` caps alembic's logger too, which logline's undo does not know.
    """
    del json_logging
    alembic = logging.getLogger("alembic")
    level = alembic.level
    yield
    alembic.setLevel(level)


@pytest.fixture(autouse=True)
def clients_closed_with_the_test() -> Iterator[None]:
    yield
    CLIENTS.close()
