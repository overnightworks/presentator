"""The deck page's seeded deck, its build and the talk host that serves it."""

from datetime import timedelta
from pathlib import Path
from typing import Final

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from presentator.contracts.decks import Artefacts, Build, BuildAttempt, Deck
from tests.presentator.application.support.fakes import FakeBuildRunner, FakeDeckStore
from tests.support.lobby import (
    A_TALK_ADDRESS,
    ADMIN,
    NOW,
    SOURCE_ID,
    THE_EXAMPLE_RUN,
    GivenDecks,
)
from tests.support.talk_support import two_hosts

SLUG: Final = "hello-deck"
TITLE: Final = "Hello Co-Presenter"
COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
BUILT_AGO: Final = timedelta(minutes=12)


def a_build() -> Build:
    """A build of the example talk that switched over a while ago."""
    return Build(
        run=THE_EXAMPLE_RUN,
        commit=COMMIT,
        built_at=NOW - BUILT_AGO,
        toolchain=None,
    )


def a_builder_serving(*, talk: Path, pdf: Path) -> FakeBuildRunner:
    """A build runner that serves the example run's talk and PDF from those paths."""
    return FakeBuildRunner(served={THE_EXAMPLE_RUN: Artefacts(directory=talk, pdf=pdf)})


def a_deck_store(
    *,
    slug: str = SLUG,
    built: Build | None = None,
    attempt: BuildAttempt | None = None,
    owner: str = ADMIN,
) -> FakeDeckStore:
    store = FakeDeckStore()
    store.put(
        Deck(
            slug=slug,
            title=TITLE,
            changed_at=NOW,
            owner_id=owner,
            source_id=SOURCE_ID,
            commit=COMMIT,
            build=None,
            attempt=None,
        ),
    )
    if built is not None:
        store.put_build(slug, built)
    if attempt is not None:
        store.put_attempt(slug, attempt)
    return store


def a_talk_host(engine: Engine, given: GivenDecks) -> TestClient:
    """A browser signed in at the talk host of an instance with these decks."""
    return two_hosts(engine, given=given).signed_in_at(A_TALK_ADDRESS)
