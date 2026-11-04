"""The lobby the browser drive runs against, and the OpenAPI of its JSON routes.

`serve` composes the whole lobby over the in-memory stores the route tests
use, with the app's real build, inside an outer app, and serves it at an http
origin on this machine; nobody signs in at an issuer. The outer app's one
drive-only route sits outside the lobby's gate: it makes a new person (a role,
a language, deck records and a source of their own) and answers their id and
session cookies, so each test of the drive signs in as its own person. A second
drive-only route adds a deck record to a person it made. `openapi` writes the
document the lobby's wire types are generated from, so the empty-regeneration
check holds the committed one to the routes.
"""

# ruff: noqa: T201 - this harness's one output is the line naming where it serves

import argparse
import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from http.cookies import SimpleCookie
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Literal, Self

import uvicorn
from babel.messages.pofile import read_po, write_po
from fastapi import FastAPI, Response
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator
from webauth.config import WebAuthConfig
from webauth.cookies import issue_session_cookies

from presentator.adapters.catalog import CATALOG_DIRECTORY, load_lobby_text
from presentator.api.lobby_shell import LobbyShell
from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    Deck,
    DeckState,
)
from presentator.contracts.models import Role, User
from presentator.contracts.preferences import PersonPreferences
from presentator.contracts.text import DEFAULT_LANGUAGE_TAG, Catalogs
from presentator.host.main import web_auth_config_for
from tests.presentator.application.support.fakes import (
    FakeDeckStore,
    FakePeople,
    FakePersonPreferencesStore,
    FakeSourceStore,
)
from tests.support.lobby import (
    BUILT_TALK,
    CATALOGS,
    EXPORTED_PDF,
    NOW,
    SOURCE_ID,
    GivenDecks,
    SessionsOnly,
    SigningIn,
    a_configured_source,
    a_crowd,
    a_lobby,
    a_lobby_app,
    a_preferences,
    an_installed_auth,
)

_LOOPBACK: Final = "127.0.0.1"
_DRIVE_HOST: Final = "localhost"
_JSON_ROUTES: Final = "/api/"
_MACHINE_ROUTES: Final = "/api/admin/"
_SCHEMA_REFERENCE: Final = "#/components/schemas/"
_SECRET: Final = SecretStr("a drive's own key of at least 32 bytes")
_PERSON_ROUTE: Final = "/drive/person"
_DECK_ROUTE: Final = f"{_PERSON_ROUTE}/{{person_id}}/deck"
# Chromium words ages in this language and has none for `eo`; it is no shipped
# catalog, so the drive builds it from English (`_second_language`).
SECOND_LANGUAGE_TAG: Final = "de"
_SECOND_LANGUAGE_NAME: Final = "German"
_SOME_COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
_ATTEMPT_COMMIT: Final = "e91c5ad4f3b2a1908877665544332211aabbccdd"
_A_PDF_NOBODY_WROTE: Final = EXPORTED_PDF.with_name("nobody-wrote-this.pdf")
_LANGUAGE_NAME_MESSAGE: Final = "language_name"

type JsonNode = dict[str, JsonNode] | list[JsonNode] | str | int | float | bool | None


class FailureRecord(BaseModel):
    """Why an attempt failed, as `BuildFailure` carries it."""

    model_config = ConfigDict(frozen=True)

    reason: BuildFailureReason
    text: str | None = None
    written_megabytes: int | None = None
    allowed_megabytes: int | None = None
    allowed_entries: int | None = None


class AttemptRecord(BaseModel):
    """The build that ran last without becoming the talk."""

    model_config = ConfigDict(frozen=True)

    commit: str = _ATTEMPT_COMMIT
    started_at: datetime = NOW
    failure: FailureRecord | None = None


class DeckRecord(BaseModel):
    """One deck of a person: its state, title, the instant it changed and its builds.

    A ready deck has a build, made at `built_at` (the drive's `NOW` unless
    given); a building or failed deck has one only when `built_at` is given,
    and has an attempt (a default one unless given). `pdf` says the build's PDF
    file is gone.
    """

    model_config = ConfigDict(frozen=True)

    state: DeckState
    title: str
    changed_at: datetime
    built_at: datetime | None = None
    attempt: AttemptRecord | None = None
    pdf: Literal["missing"] | None = None

    @model_validator(mode="after")
    def builds_fit_the_state(self) -> Self:
        """Refuse a build or an attempt the state cannot have."""
        runs_since = self.state in {DeckState.BUILDING, DeckState.FAILED}
        has_build = self.state is DeckState.READY or (
            runs_since and self.built_at is not None
        )
        if self.built_at is not None and self.state is DeckState.NEVER_BUILT:
            message = "a deck never built has no build instant"
            raise ValueError(message)
        if self.attempt is not None and not runs_since:
            message = "only a building or failed deck has an attempt"
            raise ValueError(message)
        if self.pdf is not None and not has_build:
            message = "a deck without a build has no PDF to be missing"
            raise ValueError(message)
        return self


class NewPerson(BaseModel):
    """Who a test of the drive asks for.

    A role, the second language by name (the person's own choice, the one
    the harness builds), deck records and the address of a source of their own.
    """

    model_config = ConfigDict(frozen=True)

    role: Role
    language: Literal["second"] | None = None
    decks: tuple[DeckRecord, ...] = ()
    source: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Newcomers:
    """The stores a new person is entered into, and the names it hands out."""

    people: FakePeople
    preferences: FakePersonPreferencesStore
    decks: FakeDeckStore
    sources: FakeSourceStore
    config: WebAuthConfig
    numbers: Iterator[int]

    def enter(self, wanted: NewPerson) -> dict[str, JsonNode]:
        """Make the person with their decks, source and language; answer them."""
        number = next(self.numbers)
        person = User(
            id=f"id-of-person-{number}", username=f"person-{number}", role=wanted.role
        )
        if wanted.source is not None:
            self.sources.sources.append(
                replace(
                    a_configured_source(wanted.source),
                    id=f"source-of-{number}",
                    name=f"source-{number}",
                    owner_id=person.id,
                )
            )
        slugs: list[JsonNode] = [
            self.add_deck(person.id, record) for record in wanted.decks
        ]
        self.preferences.write(
            person.id,
            PersonPreferences(
                language_tag=SECOND_LANGUAGE_TAG if wanted.language else None
            ),
        )
        issued = Response()
        issue_session_cookies(issued, self.people.open_session_for(person), self.config)
        return {"person_id": person.id, "slugs": slugs, **_session_cookies(issued)}

    def add_deck(self, person_id: str, record: DeckRecord) -> str:
        """Add the record to that person's decks; answer its slug.

        A slug names a deck for the whole instance, so each is new and a test
        reads it from the answer rather than choosing it.
        """
        slug = f"deck-{next(self.numbers)}"
        self.decks.put(
            Deck(
                slug=slug,
                title=record.title,
                changed_at=record.changed_at,
                owner_id=person_id,
                source_id=self._source_of(person_id),
                commit=_SOME_COMMIT,
                build=None,
                attempt=None,
            )
        )
        if record.state is DeckState.READY or record.built_at is not None:
            self.decks.put_build(
                slug,
                Build(
                    directory=BUILT_TALK,
                    pdf=_A_PDF_NOBODY_WROTE if record.pdf else EXPORTED_PDF,
                    commit=_SOME_COMMIT,
                    built_at=record.built_at or NOW,
                ),
            )
        if record.state in {DeckState.BUILDING, DeckState.FAILED}:
            attempt = record.attempt or AttemptRecord()
            self.decks.put_attempt(
                slug,
                BuildAttempt(
                    commit=attempt.commit,
                    started_at=attempt.started_at,
                    outcome=BuildOutcome.RUNNING
                    if record.state is DeckState.BUILDING
                    else BuildOutcome.FAILED,
                    failure=None
                    if attempt.failure is None
                    else BuildFailure(**attempt.failure.model_dump()),
                ),
            )
        return slug

    def _source_of(self, person_id: str) -> str:
        """The person's own source, else one the instance does not carry."""
        return next(
            (item.id for item in self.sources.sources if item.owner_id == person_id),
            SOURCE_ID,
        )

    def add_deck_of(self, person_id: str, record: DeckRecord) -> dict[str, str]:
        """The drive-only route that adds a record to a person already made."""
        return {"slug": self.add_deck(person_id, record)}


def _second_language(catalogs: Catalogs) -> Catalogs:
    """The catalogs plus one built from English whose words all differ from it."""
    catalog = read_po(
        (CATALOG_DIRECTORY / f"{DEFAULT_LANGUAGE_TAG}.po").open(encoding="utf-8")
    )
    catalog.locale = SECOND_LANGUAGE_TAG
    for message in catalog:
        if message.id:
            message.string = (
                _SECOND_LANGUAGE_NAME
                if message.id == _LANGUAGE_NAME_MESSAGE
                else f"{SECOND_LANGUAGE_TAG}: {message.string}"
            )
    with TemporaryDirectory() as directory:
        written = Path(directory) / f"{SECOND_LANGUAGE_TAG}.po"
        with written.open("wb") as target:
            write_po(target, catalog)
        second = load_lobby_text(written)
    return Catalogs(by_tag={**catalogs.by_tag, SECOND_LANGUAGE_TAG: second})


def _driven_lobby(build: Path, origin: str) -> FastAPI:
    """The lobby over in-memory stores inside the outer app that makes its people."""
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
    chosen = FakePersonPreferencesStore()
    store = FakeDeckStore()
    sources = FakeSourceStore()
    lobby, _ = a_lobby_app(
        people=people,
        preferences=a_preferences(catalogs=_second_language(CATALOGS), people=chosen),
        given=GivenDecks(store=store, source_store=sources),
        signing_in=SigningIn(sessions=people.sessions, auth=auth),
        shell=shell,
    )
    newcomers = _Newcomers(
        people=people,
        preferences=chosen,
        decks=store,
        sources=sources,
        config=config,
        numbers=itertools.count(1),
    )
    outer = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    outer.add_api_route(_PERSON_ROUTE, newcomers.enter, methods=["POST"])
    outer.add_api_route(_DECK_ROUTE, newcomers.add_deck_of, methods=["POST"])
    outer.mount("/", lobby)
    return outer


def _session_cookies(issued: Response) -> dict[str, JsonNode]:
    """The issued cookies as Playwright's `addCookies` takes them."""
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
    return {"cookies": cookies}


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


def _serve(build: Path, port: int) -> None:
    origin = f"http://{_DRIVE_HOST}:{port}"
    app = _driven_lobby(build, origin)
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
    openapi = commands.add_parser("openapi", help="write the JSON routes' OpenAPI")
    openapi.add_argument("target", type=Path, help="the document to write")
    arguments = parser.parse_args()
    if arguments.command == "serve":
        _serve(arguments.build, arguments.port)
    else:
        _write_openapi(arguments.target)


if __name__ == "__main__":
    main()
