"""Which module names the speech client; import-linter squashes an external package."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "src" / "presentator"
CLIENT = "speech_runtime_client"
THE_ADAPTER = SOURCE / "adapters" / "speech.py"


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
    return modules


def names(module: str, library: str) -> bool:
    return module == library or module.startswith(f"{library}.")


@pytest.mark.parametrize(
    "path",
    sorted(SOURCE.rglob("*.py")),
    ids=lambda path: str(path.relative_to(SOURCE)),
)
def test_only_the_speech_adapter_names_the_speech_client(path: Path) -> None:
    """K1: the wire has one owner in this repository."""
    client = {module for module in imported_modules(path) if names(module, CLIENT)}

    assert (client != set()) == (path == THE_ADAPTER)
