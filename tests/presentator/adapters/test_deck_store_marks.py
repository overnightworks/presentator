"""The mark a deck row carries while its folder is missing, and the purge by it."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import SqliteSourceRunStore, SqliteSourceStore
from presentator.adapters.identity import TokenIdentifierFactory
from presentator.adapters.schema import create_tables
from presentator.adapters.secrets import secret_box
from presentator.application.build_retention import REMOVED_DECK_KEPT
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

_MICROSECOND = timedelta(microseconds=1)
_DAY = timedelta(days=1)
_NOTHING = timedelta()


@pytest.fixture
def missed_at() -> datetime:
    return datetime(2026, 1, 20, 9, tzinfo=UTC)


@pytest.fixture
def another_source_id() -> str:
    return "a-second-source"


def a_deck_store(tmp_path: Path) -> SqliteDeckStore:
    database = tmp_path / "presentator.sqlite3"
    create_tables(database)
    return SqliteDeckStore(database=database)


def slugs_of(store: SqliteDeckStore, source_id: str = SOURCE_ID) -> set[str]:
    return {deck.slug for deck in store.for_source(source_id)}


def missed(store: SqliteDeckStore, *, at: datetime, source_id: str = SOURCE_ID) -> None:
    """A pass that read the source and found none of its folders."""
    store.mark_removed_except(frozenset(), source_id=source_id, at=at)


def test_only_that_sources_rows_marked_by_the_cutoff_go(
    tmp_path: Path, missed_at: datetime, another_source_id: str
) -> None:
    """#511 matrix 18."""
    store = a_deck_store(tmp_path)
    for slug in ("long-gone", "just-gone", "listed"):
        store.put(a_deck(slug))
    store.put(a_deck("theirs", source_id=another_source_id))
    store.mark_removed_except(
        frozenset({"just-gone", "listed"}), source_id=SOURCE_ID, at=missed_at
    )
    store.mark_removed_except(
        frozenset({"listed"}), source_id=SOURCE_ID, at=missed_at + _DAY
    )
    missed(store, at=missed_at, source_id=another_source_id)

    removed = store.remove_marked_before(SOURCE_ID, before=missed_at + _DAY / 2)

    assert removed == 1
    assert slugs_of(store) == {"just-gone", "listed"}
    assert slugs_of(store, another_source_id) == {"theirs"}


@pytest.mark.parametrize(
    ("marked_after", "before_after", "removed"),
    [
        pytest.param(_NOTHING, _NOTHING, 1, id="whole-second-at-the-cutoff"),
        pytest.param(_NOTHING, -_MICROSECOND, 0, id="whole-second-after-it"),
        pytest.param(_NOTHING, _MICROSECOND, 1, id="whole-second-before-it"),
        pytest.param(_MICROSECOND, _NOTHING, 0, id="microsecond-after-it"),
        pytest.param(_MICROSECOND, _MICROSECOND, 1, id="microsecond-at-it"),
        pytest.param(-_MICROSECOND, _NOTHING, 1, id="microsecond-before-it"),
    ],
)
def test_the_cutoff_holds_to_the_microsecond(
    tmp_path: Path,
    missed_at: datetime,
    marked_after: timedelta,
    before_after: timedelta,
    removed: int,
) -> None:
    """#511 matrix 19."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=missed_at + marked_after)

    assert (
        store.remove_marked_before(SOURCE_ID, before=missed_at + before_after)
        == removed
    )


def test_a_second_miss_keeps_the_first_instant(
    tmp_path: Path, missed_at: datetime
) -> None:
    """#511 matrix 20."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=missed_at)
    missed(store, at=missed_at + 10 * _DAY)

    assert store.remove_marked_before(SOURCE_ID, before=missed_at) == 1


def test_a_folder_seen_again_starts_a_new_count_at_its_next_miss(
    tmp_path: Path, missed_at: datetime
) -> None:
    """#511 matrix 20."""
    store = a_deck_store(tmp_path)
    store.put(a_deck())
    missed(store, at=missed_at)
    store.mark_removed_except(
        frozenset({a_deck().slug}), source_id=SOURCE_ID, at=missed_at + _DAY
    )
    missed_again = missed_at + 2 * _DAY
    missed(store, at=missed_again)

    assert store.remove_marked_before(SOURCE_ID, before=missed_again - _DAY) == 0
    assert store.remove_marked_before(SOURCE_ID, before=missed_again) == 1


def test_a_refresh_over_the_tables_deletes_a_deck_missed_for_the_kept_period(
    tmp_path: Path, missed_at: datetime
) -> None:
    """#511 matrix 21."""
    a_git_host_credential = "the read-only words only this test made up"
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
            access_secret=a_git_host_credential,
            hook_secret_hash=bytes(32),
        ),
    )
    assert source is not None
    store = SqliteDeckStore(database=database)
    folders = FakeDeckFolders(carried={source.id: (a_folder("kundenfeedback"),)})
    builder = FakeBuildRunner()
    clock = FrozenClock(instant=missed_at)
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

    clock.instant = missed_at + REMOVED_DECK_KEPT - _MICROSECOND
    decks.refresh()
    assert slugs_of(store, source.id) == {"kundenfeedback"}
    assert builder.on_disk

    clock.instant = missed_at + REMOVED_DECK_KEPT
    decks.refresh()
    assert slugs_of(store, source.id) == set()
    assert not builder.on_disk
