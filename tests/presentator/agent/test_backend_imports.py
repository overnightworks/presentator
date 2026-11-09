"""Which module names the client; import-linter squashes an external package."""

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[3] / "src" / "presentator"
CLIENT = "agent_runtime_client"
SETTINGS_MODULE = SOURCE / "agent" / "settings.py"
CONNECTOR = SOURCE / "agent" / "backend.py"
MCP_FRONT = SOURCE / "agent" / "mcp.py"
# The routes declare their tool decision with the one module import-linter cannot
# separate from the rest of the client.
API = SOURCE / "api"
ROUTE_DECISIONS = f"{CLIENT}.tools"
# The wire, the run stream and the capability stay with the retiring service.
UNUSED_PARTS = ("wire", "run_stream", "sse", "capability")
# The MCP front is the one owner of the client's MCP server and its wire.
MCP_PARTS = ("mcp_server", "mcp_wire")


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
def test_the_agent_package_names_the_client_and_api_only_its_tools_module(
    path: Path,
) -> None:
    """C14: the connector and the MCP front name the client; settings, its config.

    The routes of `api` name its tools module and no other.
    """
    client = {module for module in imported_modules(path) if names(module, CLIENT)}

    assert not any(
        names(module, f"{CLIENT}.{part}") for module in client for part in UNUSED_PARTS
    )
    if path != MCP_FRONT:
        assert not any(
            names(module, f"{CLIENT}.{part}") for module in client for part in MCP_PARTS
        )
    if path == SETTINGS_MODULE:
        assert client == {f"{CLIENT}.config", f"{CLIENT}.config.AgentConfig"}
    elif path.is_relative_to(API):
        assert all(names(module, ROUTE_DECISIONS) for module in client)
    elif path not in {CONNECTOR, MCP_FRONT}:
        assert client == set()
