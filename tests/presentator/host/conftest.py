"""Fixtures the worker's host tests share."""

from pathlib import Path

import pytest

from tests.presentator.host.support.sealed_snapshot import stage_sealed_root


@pytest.fixture
def sealed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return stage_sealed_root(tmp_path, monkeypatch)
