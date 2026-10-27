"""Fixtures shared by the script tests: the script loader, and for the cutover tests the module, a tmp home and the host fake."""

import importlib
import sys
from collections.abc import Callable, Iterator
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
def load_script(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., ModuleType]]:
    """Import a script by name; drop the modules it brought in at teardown."""
    before = set(sys.modules)

    def load(name: str, subdirectory: str = "") -> ModuleType:
        monkeypatch.setattr(sys, "path", [str(SCRIPTS / subdirectory), *sys.path])
        return importlib.import_module(name)

    yield load
    for name in set(sys.modules) - before:
        file = getattr(sys.modules[name], "__file__", None)
        if file is not None and Path(file).is_relative_to(SCRIPTS):
            del sys.modules[name]


@pytest.fixture
def cutover(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("cutover")


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
