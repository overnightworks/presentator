"""The lobby the browser drive runs against, and the OpenAPI of its JSON routes.

`serve` composes the whole lobby over the in-memory stores the route tests
use, with the app's real build and one person signed in, and serves it at an
http origin on this machine; nobody signs in at an issuer. Before it serves,
it writes the browser's storage state, so the drive opens the lobby as that
person. `openapi` writes the document the lobby's wire types are generated
from, so the empty-regeneration check holds the committed one to the routes.
"""

# ruff: noqa: T201 - this harness's one output is the line naming where it serves

import argparse
import json
from dataclasses import replace
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Final

import uvicorn
from fastapi import FastAPI, Response
from pydantic import SecretStr
from webauth.cookies import issue_session_cookies

from presentator.api.lobby_shell import LobbyShell
from presentator.contracts.models import Role, User
from presentator.host.main import web_auth_config_for
from tests.support.lobby import (
    SessionsOnly,
    SigningIn,
    a_crowd,
    a_lobby,
    a_lobby_app,
    an_installed_auth,
)

_LOOPBACK: Final = "127.0.0.1"
_DRIVE_HOST: Final = "localhost"
_JSON_ROUTES: Final = "/api/"
_MACHINE_ROUTES: Final = "/api/admin/"
_SCHEMA_REFERENCE: Final = "#/components/schemas/"
_SECRET: Final = SecretStr("a drive's own key of at least 32 bytes")
_DRIVER: Final = User(id="id-of-the-driver", username="driver", role=Role.ADMIN)

type JsonNode = dict[str, JsonNode] | list[JsonNode] | str | int | float | bool | None


def _driven_lobby(build: Path, origin: str) -> tuple[FastAPI, Response]:
    """The lobby over in-memory stores, and the cookies of its one signed-in person."""
    people = a_crowd()
    clock = people.clock
    shell = LobbyShell.read(build)
    config = replace(
        web_auth_config_for(_SECRET, trusted_proxies="", origin=origin),
        wall_clock=lambda: clock.now().timestamp(),
    )
    auth = replace(
        an_installed_auth(
            clock,
            transaction=SessionsOnly(people.sessions),
            shell=shell,
        ),
        config=config,
    )
    app, _ = a_lobby_app(
        people=people,
        signing_in=SigningIn(sessions=people.sessions, auth=auth),
        shell=shell,
    )
    issued = Response()
    issue_session_cookies(issued, people.open_session_for(_DRIVER), config)
    return app, issued


def _storage_state(issued: Response) -> dict[str, JsonNode]:
    """The issued cookies as Playwright's storage state carries them."""
    jar = SimpleCookie()
    for name, value in issued.raw_headers:
        if name == b"set-cookie":
            jar.load(value.decode())
    cookies: list[JsonNode] = [
        {
            "name": morsel.key,
            "value": morsel.value,
            "domain": _DRIVE_HOST,
            "path": morsel["path"],
            "expires": -1,
            "httpOnly": bool(morsel["httponly"]),
            "secure": bool(morsel["secure"]),
            "sameSite": str(morsel["samesite"]).capitalize(),
        }
        for morsel in jar.values()
    ]
    return {"cookies": cookies, "origins": []}


def _references(node: JsonNode) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str):
            found.add(reference.removeprefix(_SCHEMA_REFERENCE))
        children = list(node.values())
    elif isinstance(node, list):
        children = node
    else:
        children = []
    for child in children:
        found |= _references(child)
    return found


def json_route_document() -> dict[str, JsonNode]:
    """The lobby's OpenAPI document cut to its JSON routes and the schemas they use.

    The machine routes under `/api/admin/` are hal's, not the app's, and stay
    out (ADR 0020).
    """
    spec: dict[str, JsonNode] = a_lobby().app.openapi()
    all_paths = spec["paths"]
    all_schemas = spec["components"]
    assert isinstance(all_paths, dict)
    assert isinstance(all_schemas, dict)
    schemas = all_schemas["schemas"]
    assert isinstance(schemas, dict)
    paths: dict[str, JsonNode] = {
        path: operations
        for path, operations in all_paths.items()
        if path.startswith(_JSON_ROUTES) and not path.startswith(_MACHINE_ROUTES)
    }
    reached: set[str] = set()
    pending = _references(paths)
    while pending:
        name = pending.pop()
        reached.add(name)
        pending |= _references(schemas[name]) - reached
    return {
        **{
            key: value
            for key, value in spec.items()
            if key not in {"paths", "components"}
        },
        "paths": paths,
        "components": {"schemas": {name: schemas[name] for name in sorted(reached)}},
    }


def _serve(build: Path, port: int, storage_state: Path) -> None:
    origin = f"http://{_DRIVE_HOST}:{port}"
    app, issued = _driven_lobby(build, origin)
    storage_state.parent.mkdir(parents=True, exist_ok=True)
    storage_state.write_text(json.dumps(_storage_state(issued)), encoding="utf-8")
    print(f"the driven lobby serves at {origin}", flush=True)
    uvicorn.run(app, host=_LOOPBACK, port=port, log_level="warning")


def _write_openapi(target: Path) -> None:
    document = json.dumps(json_route_document(), indent=2, sort_keys=True)
    target.write_text(f"{document}\n", encoding="utf-8")


def main() -> None:
    """Serve the driven lobby, or write the OpenAPI document of its JSON routes."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the lobby for the drive")
    serve.add_argument("build", type=Path, help="the lobby app's build directory")
    serve.add_argument("port", type=int, help="the port on this machine")
    serve.add_argument(
        "storage_state", type=Path, help="where the signed-in browser state goes"
    )
    openapi = commands.add_parser("openapi", help="write the JSON routes' OpenAPI")
    openapi.add_argument("target", type=Path, help="the document to write")
    arguments = parser.parse_args()
    if arguments.command == "serve":
        _serve(arguments.build, arguments.port, arguments.storage_state)
    else:
        _write_openapi(arguments.target)


if __name__ == "__main__":
    main()
