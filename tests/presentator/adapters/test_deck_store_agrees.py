"""The SQLite and the Postgres deck store answer the same to the same writes."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    Deck,
    Source,
    SourceWrite,
    ToolchainIdentity,
)
from tests.presentator.adapters.support.deck_stores_support import (
    DeckAdapters,
    postgres_deck_adapters,
    sqlite_deck_adapters,
)
from tests.presentator.adapters.support.source_stores_support import NumberedKeyPairs

_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
_HOUR = timedelta(hours=1)
_TWO_HOURS_EAST = timezone(timedelta(hours=2))
_ADMIN = "admin-é"
_OTHER_ADMIN = "other-admin"
_DIGEST = "ab" * 32
_ANOTHER_DIGEST = "cd" * 32


@pytest.fixture
def numbered_keys() -> NumberedKeyPairs:
    return NumberedKeyPairs()


@pytest.fixture
def sqlite_stores(tmp_path: Path, numbered_keys: NumberedKeyPairs) -> DeckAdapters:
    return sqlite_deck_adapters(tmp_path, numbered_keys)


@pytest.fixture
def postgres_stores(
    database_url: str, numbered_keys: NumberedKeyPairs
) -> Iterator[DeckAdapters]:
    with postgres_deck_adapters(database_url, numbered_keys) as adapters:
        yield adapters


def _a_source(adapters: DeckAdapters, name: str, owner_id: str) -> Source:
    adapters.sources.seed_owner(owner_id)
    source = adapters.sources.sources.add(
        SourceWrite(
            name=name,
            url=f"https://git.example.test/{name}.git",
            ref="main",
            owner_id=owner_id,
            access_secret=f"token of {name}",
            hook_secret_hash=b"digest",
        )
    )
    assert source is not None
    return source


def _a_deck(
    slug: str,
    source: Source,
    *,
    title: str = "A talk",
    commit: str = "commit-1",
    changed_at: datetime = _NOON,
) -> Deck:
    return Deck(
        slug=slug,
        title=title,
        changed_at=changed_at,
        owner_id=source.owner_id,
        source_id=source.id,
        commit=commit,
        build=None,
        attempt=None,
    )


def _a_build(commit: str, *, toolchain: str | None = _DIGEST) -> Build:
    return Build(
        directory=Path(f"/builds/{commit}/talk é"),
        pdf=Path(f"/builds/{commit}/deck.pdf"),
        commit=commit,
        built_at=_NOON + 2 * _HOUR,
        toolchain=None if toolchain is None else ToolchainIdentity(toolchain),
    )


def _an_attempt(
    failure: BuildFailure | None, *, outcome: BuildOutcome = BuildOutcome.FAILED
) -> BuildAttempt:
    return BuildAttempt(
        commit="commit-2",
        started_at=_NOON + 3 * _HOUR,
        outcome=outcome,
        failure=failure,
        toolchain=ToolchainIdentity(_ANOTHER_DIGEST),
    )


def _put_and_read(adapters: DeckAdapters) -> list[object]:
    """`put` and `get`, each observed by what it answers and what the row holds."""
    decks = adapters.decks
    mine = _a_source(adapters, "mine", _ADMIN)
    theirs = _a_source(adapters, "theirs", _OTHER_ADMIN)
    return [
        decks.get("talk"),
        decks.put(_a_deck("talk", mine)),
        decks.put(_a_deck("talk", theirs, title="taken over")),
        decks.get("talk"),
        decks.put(
            _a_deck(
                "talk",
                mine,
                title="é Renamed",
                commit="commit-3",
                changed_at=_NOON + _HOUR,
            )
        ),
        decks.get("talk"),
        decks.get("no-such-talk"),
        decks.put(_a_deck("another", theirs)),
        sorted(decks.all(), key=lambda deck: deck.slug),
    ]


def _build_and_attempt(adapters: DeckAdapters) -> list[object]:
    """`put_build` and `put_attempt`, each observed by the row they leave."""
    decks = adapters.decks
    mine = _a_source(adapters, "mine", _ADMIN)
    decks.put(_a_deck("talk", mine))
    observed: list[object] = []

    decks.put_attempt("talk", _an_attempt(None, outcome=BuildOutcome.RUNNING))
    observed.append(decks.get("talk"))
    for failure in (
        BuildFailure.said('it printed é and "quotes"'),
        BuildFailure.without_words(),
        BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=9,
            allowed_megabytes=5,
        ),
        BuildFailure(reason=BuildFailureReason.OUTPUT_ENTRIES, allowed_entries=7),
        BuildFailure(
            reason=BuildFailureReason.DECK_MEGABYTES,
            allowed_megabytes=2,
            allowed_entries=3,
        ),
    ):
        decks.put_attempt("talk", _an_attempt(failure))
        observed.append(decks.get("talk"))
    decks.put_attempt("talk", None)
    observed.append(decks.get("talk"))

    decks.put_attempt("talk", _an_attempt(BuildFailure.said("again")))
    decks.put_build("talk", _a_build("commit-2"))
    observed.append(decks.get("talk"))
    decks.put(_a_deck("talk", mine, commit="commit-4"))
    decks.put_attempt("talk", _an_attempt(BuildFailure.said("while it stands")))
    observed.append(decks.get("talk"))
    decks.put_build("talk", _a_build("commit-5", toolchain=None))
    observed.append(decks.get("talk"))
    decks.put_build("no-such-talk", _a_build("commit-6"))
    decks.put_attempt("no-such-talk", _an_attempt(None, outcome=BuildOutcome.RUNNING))
    observed.append(decks.get("no-such-talk"))
    return observed


def _marked_and_removed(adapters: DeckAdapters) -> list[object]:
    """The marks, the purge by them, and the removal of a source's rows."""
    decks = adapters.decks
    mine = _a_source(adapters, "mine", _ADMIN)
    theirs = _a_source(adapters, "theirs", _OTHER_ADMIN)
    slugs = ("kept", "gone-first", "gone-too", 'ünïcode, "quoted" {slug}')
    for slug in slugs:
        decks.put(_a_deck(slug, mine))
    decks.put(_a_deck("theirs", theirs))
    observed: list[object] = []

    def see() -> None:
        observed.append(sorted(decks.all(), key=lambda deck: deck.slug))
        observed.append(sorted(decks.for_source(mine.id), key=lambda deck: deck.slug))

    decks.mark_removed_except(frozenset(slugs[:1]), source_id=mine.id, at=_NOON)
    see()
    decks.mark_removed_except(
        frozenset(slugs[:1]), source_id=mine.id, at=_NOON + 5 * _HOUR
    )
    observed.append(decks.remove_marked_before(mine.id, before=_NOON - _HOUR))
    decks.mark_removed_except(frozenset(slugs[:2]), source_id=mine.id, at=_NOON + _HOUR)
    see()
    observed.append(decks.get("gone-first"))
    observed.append(
        decks.remove_marked_before(
            mine.id, before=(_NOON + 4 * _HOUR).astimezone(_TWO_HOURS_EAST)
        )
    )
    see()
    observed.append(decks.remove_marked_before(mine.id, before=_NOON + 5 * _HOUR))
    see()
    decks.mark_removed_except(frozenset(), source_id=mine.id, at=_NOON + 6 * _HOUR)
    see()
    decks.mark_removed_except(frozenset(), source_id="no-such-source", at=_NOON)
    marked_at = _NOON + 6 * _HOUR
    observed.append(
        decks.remove_marked_before(
            mine.id, before=marked_at - timedelta(microseconds=1)
        )
    )
    observed.append(decks.remove_marked_before(mine.id, before=marked_at))
    see()
    decks.put(_a_deck("kept", mine))
    decks.remove_for_source(mine.id)
    decks.remove_for_source("no-such-source")
    see()
    observed.append(decks.for_source(theirs.id))
    return observed


@pytest.mark.parametrize(
    "scenario", [_put_and_read, _build_and_attempt, _marked_and_removed]
)
def test_the_same_writes_read_the_same_through_both_adapters(
    scenario: Callable[[DeckAdapters], list[object]],
    sqlite_stores: DeckAdapters,
    postgres_stores: DeckAdapters,
    numbered_keys: NumberedKeyPairs,
) -> None:
    through_sqlite = scenario(sqlite_stores)
    numbered_keys.restart()
    through_postgres = scenario(postgres_stores)

    assert through_postgres == through_sqlite
    # Aware datetimes compare by instant; their rendering shows the offset read.
    assert repr(through_postgres) == repr(through_sqlite)
    assert any(each is not None for each in through_sqlite)


def test_a_deck_written_at_another_offset_is_the_same_instant_through_both_adapters(
    sqlite_stores: DeckAdapters, postgres_stores: DeckAdapters
) -> None:
    elsewhere = _NOON.astimezone(_TWO_HOURS_EAST)
    read_back: list[tuple[datetime, datetime, datetime]] = []
    for adapters in (sqlite_stores, postgres_stores):
        source = _a_source(adapters, "mine", _ADMIN)
        adapters.decks.put(_a_deck("talk", source, changed_at=elsewhere))
        adapters.decks.put_build("talk", replace(_a_build("c"), built_at=elsewhere))
        adapters.decks.put_attempt(
            "talk",
            replace(
                _an_attempt(None, outcome=BuildOutcome.RUNNING), started_at=elsewhere
            ),
        )
        deck = adapters.decks.get("talk")
        assert deck is not None
        assert deck.build is not None
        assert deck.attempt is not None
        read_back.append(
            (deck.changed_at, deck.build.built_at, deck.attempt.started_at)
        )

    assert read_back == [(_NOON, _NOON, _NOON)] * 2


def test_a_postgres_deck_of_a_source_that_does_not_exist_is_an_error_not_a_refusal(
    postgres_stores: DeckAdapters,
) -> None:
    source = _a_source(postgres_stores, "mine", _ADMIN)
    orphan = replace(_a_deck("talk", source), source_id="no-such-source")

    with pytest.raises(IntegrityError):
        postgres_stores.decks.put(orphan)
