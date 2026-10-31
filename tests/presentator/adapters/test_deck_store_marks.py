"""The mark a deck row carries while its folder is missing, and the purge by it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import SqliteSourceRunStore, SqliteSourceStore
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.schema import create_tables
from presentator.adapters.secrets import secret_box
from presentator.application.decks import Decks
from presentator.contracts.decks import SourceWrite
from tests.presentator.adapters.support.key_draft_support import a_key_drafts_store
from tests.presentator.application.support.decks_support import a_folder
from tests.presentator.application.support.fakes import (
    FakeBuildRunner,
    FakeConnectionChecker,
    FakeDeckFolders,
    FakeLocalMount,
    FakeToolchainThemes,
    FakeWebhookSecrets,
    FrozenClock,
)
from tests.support.deck_source_support import OWNER, SOURCE_ID, a_deck

_MISSED_AT = datetime(2026, 1, 20, 9, tzinfo=UTC)
_ANOTHER_SOURCE_ID = "a-second-source"
_MICROSECOND = timedelta(microseconds=1)
_DAY = timedelta(days=1)
_WHAT_THE_GIT_HOST_EXPECTS = "the read-only words only this test made up"


def a_deck_store(tmp_path: Path) -> SqliteDeckStore:
    database = tmp_path / "presentator.sqlite3"
    create_tables(database)
    return SqliteDeckStore(database=database)


def slugs_of(store: SqliteDeckStore, source_id: str = SOURCE_ID) -> set[str]:
    return {deck.slug for deck in store.for_source(source_id)}


def missed(store: SqliteDeckStore, *, at: datetime, source_id: str = SOURCE_ID) -> None:
    """A pass that read the source and found none of its folders."""
    store.mark_removed_except(frozenset(), source_id=source_id, at=at)


def test_only_that_sources_rows_marked_by_the_cutoff_go(tmp_path: Path) -> None:
    """#511 matrix 18."""
    store = a_deck_store(tmp_path)
    for slug in ("long-gone", "just-gone", "listed"):
        store.put(a_deck(slug))
    store.put(a_deck("theirs", source_id=_ANOTHER_SOURCE_ID))
    store.mark_removed_except(
        frozenset({"just-gone", "listed"}), source_id=SOURCE_ID, at=_MISSED_AT
    )
    store.mark_removed_except(
        frozenset({"listed"}), source_id=SOURCE_ID, at=_MISSED_AT + _DAY
    )
    missed(store, at=_MISSED_AT, source_id=_ANOTHER_SOURCE_ID)

    removed = store.remove_marked_before(SOURCE_ID, before=_MISSED_AT + _DAY / 2)

    assert removed == 1
    assert slugs_of(store) == {"just-gone", "listed"}
    assert slugs_of(store, _ANOTHER_SOURCE_ID) == {"theirs"}


@pytest.mark.parametrize(
    ("marked_at", "before", "removed"),
    [
        pytest.param(_MISSED_AT, _MISSED_AT, 1, id="whole-second-at-the-cutoff"),
        pytest.param(
            _MISSED_AT, _MISSED_AT - _MICROSECOND, 0, id="whole-second-after-it"
        ),
        pytest.param(
            _MISSED_AT, _MISSED_AT + _MICROSECOND, 1, id="whole-second-before-it"
        ),
        pytest.param(
            _MISSED_AT + _MICROSECOND, _MISSED_AT, 0, id="microsecond-after-it"
        ),
        pytest.param(
            _MISSED_AT + _MICROSECOND,
            _MISSED_AT + _MICROSECOND,
            1,
            id="microsecond-at-it",
        ),
        pytest.param(
            _MISSED_AT - _MICROSECOND, _MISSED_AT, 1, id="microsecond-before-it"
        ),
    ],
)
def test_the_cutoff_holds_to_the_microsecond(
    tmp_path: Path, marked_at: datetime, before: datetime, removed: int
) -> None:
    """#511 matrix 19."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=marked_at)

    assert store.remove_marked_before(SOURCE_ID, before=before) == removed


def test_a_second_miss_keeps_the_first_instant(tmp_path: Path) -> None:
    """#511 matrix 20."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=_MISSED_AT)
    missed(store, at=_MISSED_AT + 10 * _DAY)

    assert store.remove_marked_before(SOURCE_ID, before=_MISSED_AT) == 1


def test_a_folder_seen_again_starts_a_new_count_at_its_next_miss(
    tmp_path: Path,
) -> None:
    """#511 matrix 20."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=_MISSED_AT)
    store.mark_removed_except(
        frozenset({a_deck().slug}), source_id=SOURCE_ID, at=_MISSED_AT + _DAY
    )
    missed_again = _MISSED_AT + 2 * _DAY
    missed(store, at=missed_again)

    assert store.remove_marked_before(SOURCE_ID, before=missed_again - _DAY) == 0
    assert store.remove_marked_before(SOURCE_ID, before=missed_again) == 1


def test_a_refresh_over_the_tables_deletes_a_deck_missed_thirty_days(
    tmp_path: Path,
) -> None:
    """#511 matrix 21."""
    database = tmp_path / "presentator.sqlite3"
    create_tables(database)
    sources = SqliteSourceStore(
        database=database,
        identifiers=TokenIdentifierFactory(),
        box=secret_box("the key only this test's instance carries"),
    )
    source = sources.add(
        SourceWrite(
            name="decks",
            url="git@example.invalid:decks.git",
            ref="main",
            owner_id=OWNER.id,
            access_secret=_WHAT_THE_GIT_HOST_EXPECTS,
            hook_secret_hash=bytes(32),
        ),
    )
    assert source is not None
    store = SqliteDeckStore(database=database)
    folders = FakeDeckFolders(carried={source.id: (a_folder("kundenfeedback"),)})
    builder = FakeBuildRunner()
    clock = FrozenClock(instant=_MISSED_AT)
    decks = Decks(
        sources=sources,
        key_drafts=a_key_drafts_store(database),
        folders=folders,
        store=store,
        builder=builder,
        source_runs=SqliteSourceRunStore(database=database),
        checker=FakeConnectionChecker(),
        toolchain_themes=FakeToolchainThemes(),
        build_bound=timedelta(minutes=5),
        fingerprint_key=b"what only this test's instance signs a fingerprint with",
        clock=clock,
        local_mount=FakeLocalMount(),
        webhook_secrets=FakeWebhookSecrets(),
    )
    decks.refresh()
    folders.carried[source.id] = ()
    decks.refresh()

    clock.instant = _MISSED_AT + 29 * _DAY
    decks.refresh()
    assert slugs_of(store, source.id) == {"kundenfeedback"}
    assert builder.on_disk

    clock.instant = _MISSED_AT + 30 * _DAY
    decks.refresh()
    assert slugs_of(store, source.id) == set()
    assert not builder.on_disk
