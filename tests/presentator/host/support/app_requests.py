"""What the two-app-process tests ask of the apps and read from the database."""

from collections.abc import Callable, Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from threading import Barrier, Event

import httpx2
from pydantic import JsonValue
from sqlalchemy import Engine, text

from tests.presentator.host.support.app_processes import App, Browser
from tests.support.git_remote import GitRemote

AT = datetime(2026, 11, 14, 12, tzinfo=UTC)
SOURCES = "/api/sources"
CHECK = "/api/sources/new/check"
DECKS = "/api/decks"


def scalar(engine: Engine, statement: str, **parameters: str) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text(statement), parameters).scalar_one())


def scalar_text(engine: Engine, statement: str) -> str:
    with engine.connect() as connection:
        return str(connection.execute(text(statement)).scalar_one())


def texts(engine: Engine, statement: str) -> set[str]:
    with engine.connect() as connection:
        return {str(each) for each in connection.execute(text(statement)).scalars()}


def at_once[Answer](calls: list[Callable[[], Answer]]) -> list[Answer]:
    start = Barrier(len(calls))

    def go(call: Callable[[], Answer]) -> Answer:
        start.wait()
        return call()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [each.result() for each in [pool.submit(go, call) for call in calls]]


Observation = tuple[tuple[str | None, ...], ...]


@contextmanager
def watching(engine: Engine, statement: str) -> Generator[list[Observation]]:
    """Read `statement` again and again meanwhile; each answer that differs is kept."""
    seen: list[Observation] = []
    done = Event()

    def read() -> None:
        with engine.connect() as connection:
            while True:
                finished = done.is_set()
                rows = connection.execute(text(statement)).all()
                observation = tuple(
                    tuple(None if each is None else str(each) for each in row)
                    for row in rows
                )
                if not seen or seen[-1] != observation:
                    seen.append(observation)
                if finished:
                    return

    with ThreadPoolExecutor(max_workers=1) as pool:
        reading = pool.submit(read)
        try:
            yield seen
        finally:
            done.set()
        reading.result()


def admin_of(app: App) -> Browser:
    admin = Browser()
    admin.sign_in(app, "admin", admin=True)
    return admin


def checked(admin: Browser, app: App, **fields: JsonValue) -> dict[str, JsonValue]:
    """The body of a source whose address the app has probed."""
    body: dict[str, JsonValue] = {"secret": None, "key_draft_id": None, **fields}
    answer = admin.request(app, "POST", CHECK, body=body)
    assert answer.status_code == HTTPStatus.OK, answer.text
    return {**body, "fingerprint": answer.json()["fingerprint"]}


def create_source(
    admin: Browser, app: App, body: dict[str, JsonValue]
) -> httpx2.Response:
    return admin.request(app, "POST", SOURCES, body=body)


def added(admin: Browser, app: App, name: str, remote: GitRemote) -> None:
    created = create_source(admin, app, checked(admin, app, name=name, url=remote.url))
    assert created.status_code == HTTPStatus.CREATED, created.text


def fetched(admin: Browser, app: App, name: str) -> httpx2.Response:
    answer = admin.request(app, "POST", f"{SOURCES}/{name}/fetch")
    assert answer.status_code == HTTPStatus.OK, answer.text
    return answer


def listed_slugs(admin: Browser, app: App) -> list[str]:
    answer = admin.request(app, "GET", DECKS)
    assert answer.status_code == HTTPStatus.OK
    return [deck["slug"] for deck in answer.json()["decks"]]
