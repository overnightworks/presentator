"""Fixtures shared by the script tests: the loader and the k3d probe's modules."""

import importlib
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

from tests.scripts.support.k3d_world import Fixture, fixture_maker

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
def driver_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d")


@pytest.fixture
def cluster_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_cluster")


@pytest.fixture
def lives_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_lives")


@pytest.fixture
def manifests_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_k3d_manifests")


@pytest.fixture
def starts_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_starts")


@pytest.fixture
def web_module(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_web")


@pytest.fixture
def make_fixture(
    load_script: Callable[..., ModuleType], tmp_path: Path
) -> Callable[..., Fixture]:
    """A maker of k3d probe runs over a fake world."""
    return fixture_maker(load_script, tmp_path)
