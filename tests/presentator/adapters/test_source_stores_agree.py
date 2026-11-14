"""The SQLite and the Postgres source stores answer the same to the same writes."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from presentator.contracts.decks import DeployKeyDraft, Source, SourceWrite
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


def _a_write(
    name: str,
    *,
    url: str | None = None,
    folder: str = "decks/é",
    public_key: str | None = None,
) -> SourceWrite:
    return SourceWrite(
        name=name,
        url=f"https://git.example.test/{name}.git" if url is None else url,
        ref="main",
        owner_id=_ADMIN,
        access_secret=f"token of {name}",
        hook_secret_hash=name.encode(),
        folder=folder,
        public_key=public_key,
    )


def _write_and_read_sources(adapters: Adapters) -> list[object]:
    """Every `SourceStore` method, each observed by what it answers and leaves."""
    sources = adapters.sources
    for owner_id in (_ADMIN, _OTHER_ADMIN):
        adapters.seed_owner(owner_id)
    observed: list[object] = []

    first = sources.add(_a_write("first"))
    second = sources.add(_a_write("second", public_key="ssh-ed25519 AAAA"))
    observed.extend((first, second))
    observed.append(sources.add(_a_write("first", url="https://other.example.test")))
    observed.append(sources.add(_a_write("third", url=_a_write("first").url)))
    observed.append(sources.add(_a_write("fourth", folder="")))
    assert first is not None
    assert second is not None
    observed.extend(adapters.secret_of(each.id) for each in (first, second))

    observed.extend(_hooks_and_credentials(adapters, first, second))
    observed.extend(_promoted_drafts(adapters))
    observed.append(sorted(sources.all(), key=lambda each: each.id))
    sources.remove(first.id)
    sources.remove("no-such-source")
    observed.append(sorted(sources.all(), key=lambda each: each.id))
    observed.append(adapters.secret_of(first.id))
    return observed


def _hooks_and_credentials(
    adapters: Adapters, first: Source, second: Source
) -> list[object]:
    sources = adapters.sources
    observed: list[object] = [
        sources.hook_secret_hash("first"),
        sources.hook_secret_hash("none"),
        sources.put_hook_secret_hash("first", b"\x00replaced\xff"),
        sources.put_hook_secret_hash("none", b"x"),
        sources.hook_secret_hash("first"),
        sources.hook_secret_hash("second"),
    ]
    sources.put_credential(first.id, "a new token é")
    sources.put_credential("no-such-source", "unused")
    observed.append(adapters.secret_of(first.id))
    observed.append(adapters.secret_of("no-such-source"))
    observed.append(sources.renew_deploy_key(second.id))
    observed.append(sources.renew_deploy_key("no-such-source"))
    observed.append(adapters.secret_of(second.id))
    observed.append(
        next(each for each in sources.all() if each.id == second.id).public_key
    )
    return observed


def _promoted_drafts(adapters: Adapters) -> list[object]:
    sources, drafts = adapters.sources, adapters.drafts
    elsewhere = adapters.sources_opened_with(ANOTHER_INSTANCE_KEY)
    observed: list[object] = []

    draft = drafts.mint(_ADMIN, at=_NOON)
    other_admins_draft = drafts.mint(_OTHER_ADMIN, at=_NOON)
    observed.append(elsewhere.add_from_draft(_a_write("promoted"), draft_id=draft.id))
    observed.append(sources.add_from_draft(_a_write("promoted"), draft_id="no-draft"))
    observed.append(
        sources.add_from_draft(_a_write("promoted"), draft_id=other_admins_draft.id)
    )
    observed.append(sources.add_from_draft(_a_write("first"), draft_id=draft.id))
    observed.append(
        sources.add_from_draft(
            _a_write("taken-url", url=_a_write("first").url), draft_id=draft.id
        )
    )
    observed.append(drafts.count_for_owner(_ADMIN))
    promoted = sources.add_from_draft(_a_write("promoted"), draft_id=draft.id)
    observed.append(promoted)
    observed.append(None if promoted is None else adapters.secret_of(promoted.id))
    observed.append(drafts.count_for_owner(_ADMIN))
    observed.append(sources.add_from_draft(_a_write("again"), draft_id=draft.id))
    observed.append(drafts.count_for_owner(_OTHER_ADMIN))
    return observed


def test_the_same_source_writes_read_the_same_through_both_adapters(
    sqlite_stores: Adapters, postgres_stores: Adapters, numbered_keys: NumberedKeyPairs
) -> None:
    through_sqlite = _write_and_read_sources(sqlite_stores)
    numbered_keys.restart()
    through_postgres = _write_and_read_sources(postgres_stores)

    assert through_postgres == through_sqlite
    assert any(each is None for each in through_sqlite)
    assert any(isinstance(each, Source) for each in through_sqlite)


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
