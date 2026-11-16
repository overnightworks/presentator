"""Fixtures the worker's host tests share."""

from pathlib import Path

import pytest

from tests.presentator.host.support.sealed_snapshot import stage_sealed_root


@pytest.fixture
def sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return stage_sealed_root(tmp_path, monkeypatch)


@pytest.fixture(autouse=True)
def logging_undone_after_main(json_logging: None) -> None:
    """Every `main()` a host test runs configures the process's logging."""
    del json_logging
