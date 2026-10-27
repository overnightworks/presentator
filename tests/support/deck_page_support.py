"""The deck page's seeded deck, its build and the talk host that serves it."""

from datetime import timedelta
from pathlib import Path
from typing import Final

from fastapi.testclient import TestClient

from presentator.contracts.decks import Build, BuildAttempt, Deck
from tests.presentator.application.support.fakes import FakeDeckStore
from tests.support.lobby import (
    A_TALK_ADDRESS,
    ADMIN,
    BUILT_TALK,
    EXPORTED_PDF,
    NOW,
    SOURCE_ID,
    GivenDecks,
)
from tests.support.talk_support import two_hosts

SLUG: Final = "hello-deck"
TITLE: Final = "Hello Co-Presenter"
COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"
BUILT_AGO: Final = timedelta(minutes=12)


def a_build(*, talk: Path = BUILT_TALK, pdf: Path = EXPORTED_PDF) -> Build:
    """A build that switched over a while ago, as the picture's board shows it."""
    return Build(directory=talk, pdf=pdf, commit=COMMIT, built_at=NOW - BUILT_AGO)


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


def a_talk_host(tmp_path: Path, given: GivenDecks) -> TestClient:
    """A browser signed in at the talk host of an instance with these decks."""
    return two_hosts(tmp_path, given=given).signed_in_at(A_TALK_ADDRESS)
