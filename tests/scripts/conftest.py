"""Fixtures shared by the cutover tests: the module, a tmp home and the host fake."""

import importlib
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.cutover_host import (
    LISTED_PATHS,
    SURVIVORS,
    UNIT_DIRECTORY,
    UNITS,
    Host,
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def cutover(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "path", [str(SCRIPTS), *sys.path])
    return importlib.import_module("cutover")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "home"
    for name in (*LISTED_PATHS[1:4], *SURVIVORS):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "data").write_text("x")
    for name in LISTED_PATHS[4:]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("unit")
    wants = root / UNIT_DIRECTORY / "default.target.wants"
    wants.mkdir()
    for unit in UNITS:
        (wants / unit).symlink_to(root / UNIT_DIRECTORY / unit)
    live = root / "presentator-live"
    (live / ".git").mkdir(parents=True)
    (live / "speech").mkdir()
    (live / "speech" / "inside-link").symlink_to(root / "presentator-stage")
    (root / "presentator-backups" / "inside-link").symlink_to(
        root / ".cache" / "huggingface"
    )
    return root


@pytest.fixture
def host(home: Path) -> Host:
    return Host(home)
