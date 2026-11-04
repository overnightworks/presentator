"""Which module names the client; import-linter squashes an external package."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "src" / "presentator"
CLIENT = "agent_runtime_client"
SETTINGS_MODULE = SOURCE / "agent" / "settings.py"
CONNECTOR = SOURCE / "agent" / "backend.py"
# The wire, the run stream and the tool and MCP surfaces stay with the retiring service.
UNUSED_PARTS = ("wire", "run_stream", "sse", "capability", "mcp_server", "mcp_wire")


def imported_modules(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def names(module: str, library: str) -> bool:
    return module == library or module.startswith(f"{library}.")


@pytest.mark.parametrize(
    "path",
    sorted(SOURCE.rglob("*.py")),
    ids=lambda path: str(path.relative_to(SOURCE)),
)
def test_only_the_agent_package_names_the_client_and_its_settings_only_the_config(
    path: Path,
) -> None:
    """C14: the connector is the one place; settings name the config alone."""
    client = {module for module in imported_modules(path) if names(module, CLIENT)}

    assert not any(
        names(module, f"{CLIENT}.{part}") for module in client for part in UNUSED_PARTS
    )
    if path == SETTINGS_MODULE:
        assert client == {f"{CLIENT}.config", f"{CLIENT}.config.AgentConfig"}
    elif path != CONNECTOR:
        assert client == set()
