"""The SQLite and the Postgres source stores answer the same to the same writes."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from presentator.contracts.decks import DeployKeyDraft
from presentator.ports.decks import DeployKeyDrafts
from tests.presentator.adapters.support.source_stores_support import (
    ANOTHER_INSTANCE_KEY,
    Adapters,
    NumberedKeyPairs,
    postgres_adapters,
    sqlite_adapters,
    use_numbered_keys,
)

_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
_HOUR = timedelta(hours=1)
_TWO_HOURS_EAST = timezone(timedelta(hours=2))
_ADMIN = "admin-é"
_OTHER_ADMIN = "other-admin"
_THIRD_ADMIN = "third-admin"


@pytest.fixture
def numbered_keys(monkeypatch: pytest.MonkeyPatch) -> NumberedKeyPairs:
    return use_numbered_keys(monkeypatch)


@pytest.fixture
def sqlite_stores(tmp_path: Path) -> Adapters:
    return sqlite_adapters(tmp_path)


@pytest.fixture
def postgres_stores(database_url: str) -> Iterator[Adapters]:
    with postgres_adapters(database_url) as adapters:
        yield adapters


def _write_and_read_drafts(adapters: Adapters) -> list[object]:
    """Every `DeployKeyDrafts` method, each observed by what it answers and leaves."""
    drafts = adapters.drafts
    for owner_id in (_ADMIN, _OTHER_ADMIN, _THIRD_ADMIN):
        adapters.seed_owner(owner_id)
    observed: list[object] = []

    observed.append(drafts.mint(_ADMIN, at=_NOON))
    observed.append(drafts.unconsumed_for(_ADMIN, newer_than=_NOON))
    observed.append(drafts.unconsumed_for(_ADMIN, newer_than=_NOON + _HOUR))
    observed.append(drafts.unconsumed_for(_OTHER_ADMIN, newer_than=_NOON))
    observed.append(drafts.mint(_ADMIN, at=_NOON + _HOUR))
    observed.append(drafts.count_for_owner(_ADMIN))

    for owner_id, newer_than, at in (
        (_ADMIN, _NOON, _NOON + 2 * _HOUR),
        (_OTHER_ADMIN, _NOON, _NOON + 2 * _HOUR),
        (_ADMIN, _NOON + 3 * _HOUR, _NOON + 4 * _HOUR),
    ):
        observed.append(drafts.get_or_mint(owner_id, newer_than=newer_than, at=at))
    observed.append(drafts.count_for_owner(_ADMIN))

    observed.extend(_bound(drafts))
    observed.extend(_swept_and_removed(drafts))
    observed.extend(_opened_with_another_key(adapters))
    return observed


def _bound(drafts: DeployKeyDrafts) -> list[object]:
    current = drafts.unconsumed_for(_ADMIN, newer_than=_NOON)
    assert isinstance(current, DeployKeyDraft)
    return [
        drafts.bind(current.id, owner_id=_OTHER_ADMIN),
        drafts.bind("no-such-draft", owner_id=_ADMIN),
        drafts.bind(current.id, owner_id=_ADMIN),
        drafts.bind(current.id, owner_id=_ADMIN),
        drafts.count_for_owner(_ADMIN),
    ]


def _swept_and_removed(drafts: DeployKeyDrafts) -> list[object]:
    drafts.mint(_ADMIN, at=_NOON + 5 * _HOUR)
    drafts.sweep(older_than=_NOON + 2 * _HOUR)
    after_the_older_one_went = [
        drafts.count_for_owner(_OTHER_ADMIN),
        drafts.count_for_owner(_ADMIN),
    ]
    drafts.sweep(older_than=_NOON + 5 * _HOUR)
    after_the_boundary = [
        drafts.count_for_owner(_OTHER_ADMIN),
        drafts.count_for_owner(_ADMIN),
    ]
    return [
        *after_the_older_one_went,
        *after_the_boundary,
        drafts.remove_for_owner(_ADMIN),
        drafts.remove_for_owner(_ADMIN),
        drafts.count_for_owner(_ADMIN),
    ]


def _opened_with_another_key(adapters: Adapters) -> list[object]:
    """A draft the instance key cannot open is counted, replaced and consumed."""
    adapters.drafts.mint(_THIRD_ADMIN, at=_NOON)
    elsewhere = adapters.drafts_opened_with(ANOTHER_INSTANCE_KEY)
    unopened = elsewhere.unconsumed_for(_THIRD_ADMIN, newer_than=_NOON)
    replaced = elsewhere.get_or_mint(_THIRD_ADMIN, newer_than=_NOON, at=_NOON)
    return [
        unopened,
        replaced,
        adapters.drafts.unconsumed_for(_THIRD_ADMIN, newer_than=_NOON),
        elsewhere.count_for_owner(_THIRD_ADMIN),
        adapters.drafts.bind(replaced.id, owner_id=_THIRD_ADMIN),
        elsewhere.bind(replaced.id, owner_id=_THIRD_ADMIN),
    ]


def test_the_same_draft_writes_read_the_same_through_both_adapters(
    sqlite_stores: Adapters, postgres_stores: Adapters, numbered_keys: NumberedKeyPairs
) -> None:
    through_sqlite = _write_and_read_drafts(sqlite_stores)
    numbered_keys.restart()
    through_postgres = _write_and_read_drafts(postgres_stores)

    assert through_postgres == through_sqlite
    # Aware datetimes compare by instant; their rendering shows the offset read.
    assert repr(through_postgres) == repr(through_sqlite)


@pytest.mark.usefixtures("numbered_keys")
def test_a_draft_minted_at_another_offset_is_the_same_instant_through_both_adapters(
    sqlite_stores: Adapters, postgres_stores: Adapters
) -> None:
    at = _NOON.astimezone(_TWO_HOURS_EAST)
    read_back: list[datetime] = []
    for adapters in (sqlite_stores, postgres_stores):
        adapters.seed_owner(_ADMIN)
        adapters.drafts.mint(_ADMIN, at=at)
        draft = adapters.drafts.unconsumed_for(_ADMIN, newer_than=_NOON)
        assert draft is not None
        read_back.append(draft.created_at)

    assert read_back == [_NOON, _NOON]
