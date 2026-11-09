"""Fixtures shared by the script tests: the script loader."""

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

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
