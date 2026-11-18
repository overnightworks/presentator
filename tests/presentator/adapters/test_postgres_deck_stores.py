"""The Postgres stores read instants back in UTC and refuse rows naming nothing."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError

from presentator.contracts.decks import (
    Build,
    BuildAttempt,
    BuildFailure,
    BuildOutcome,
    BuildRun,
    Deck,
    Source,
    SourceRun,
    SourceRunOutcome,
    SourceWrite,
    ToolchainIdentity,
)
from presentator.contracts.preferences import (
    PersonPreferences,
    ThemeChoice,
)
from tests.presentator.adapters.support.source_stores_support import (
    Adapters,
    NumberedKeyPairs,
    postgres_adapters,
)

_NOON = datetime(2026, 11, 14, 12, 0, 0, 123456, tzinfo=UTC)
_HOUR = timedelta(hours=1)
_TWO_HOURS_EAST = timezone(timedelta(hours=2))
_ADMIN = "admin-é"
_OTHER_ADMIN = "other-admin"
_THIRD_ADMIN = "third-admin"
_PERSON = "person-é"
_OTHER_PERSON = "other-person"
_DIGEST = "ab" * 32
_ANOTHER_DIGEST = "cd" * 32


@pytest.fixture
def numbered_keys() -> NumberedKeyPairs:
    return NumberedKeyPairs()


@pytest.fixture
def postgres_stores(
    database_url: str, numbered_keys: NumberedKeyPairs
) -> Iterator[Adapters]:
    with postgres_adapters(database_url, numbered_keys) as adapters:
        yield adapters


def _a_write(
    name: str,
    *,
    url: str | None = None,
    folder: str = "decks/é",
    public_key: str | None = None,
    owner_id: str = _ADMIN,
) -> SourceWrite:
    return SourceWrite(
        name=name,
        url=f"https://git.example.test/{name}.git" if url is None else url,
        ref="main",
        owner_id=owner_id,
        access_secret=f"token of {name}",
        hook_secret_hash=name.encode(),
        folder=folder,
        public_key=public_key,
    )


def test_a_run_recorded_at_another_offset_reads_back_as_the_same_instant_in_utc(
    postgres_stores: Adapters,
) -> None:
    at = _NOON.astimezone(_TWO_HOURS_EAST)
    adapters = postgres_stores
    adapters.seed_owner(_ADMIN)
    source = adapters.sources.add(_a_write("polled"))
    assert source is not None
    adapters.runs.record(
        SourceRun(
            source_id=source.id,
            at=at,
            outcome=SourceRunOutcome.SUCCESS,
            commit="c",
            reason=None,
        )
    )
    newest = adapters.runs.newest(source.id)
    assert newest is not None
    read_back = newest.at

    assert repr(read_back) == repr(_NOON)


def test_a_draft_minted_at_another_offset_reads_back_as_the_same_instant_in_utc(
    postgres_stores: Adapters,
) -> None:
    at = _NOON.astimezone(_TWO_HOURS_EAST)
    adapters = postgres_stores
    adapters.seed_owner(_ADMIN)
    adapters.drafts.mint(_ADMIN, at=at)
    draft = adapters.drafts.unconsumed_for(_ADMIN, newer_than=_NOON)
    assert draft is not None
    read_back = draft.created_at

    assert repr(read_back) == repr(_NOON)


def test_a_postgres_source_of_an_owner_that_does_not_exist_is_an_error_not_a_taken_name(
    postgres_stores: Adapters,
) -> None:
    orphan = _a_write("orphan")

    with pytest.raises(IntegrityError):
        postgres_stores.sources.add(orphan)


def _a_source(adapters: Adapters, name: str, owner_id: str) -> Source:
    adapters.seed_owner(owner_id)
    source = adapters.sources.add(_a_write(name, owner_id=owner_id))
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
        run=BuildRun(f"run-{commit}"),
        directory=Path(f"/builds/run-{commit}/out é/talk"),
        pdf=Path(f"/builds/run-{commit}/out é/deck.pdf"),
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


def test_a_deck_written_at_another_offset_reads_back_as_the_same_instant_in_utc(
    postgres_stores: Adapters,
) -> None:
    elsewhere = _NOON.astimezone(_TWO_HOURS_EAST)
    adapters = postgres_stores
    source = _a_source(adapters, "mine", _ADMIN)
    adapters.decks.put(_a_deck("talk", source, changed_at=elsewhere))
    adapters.decks.put_build("talk", replace(_a_build("c"), built_at=elsewhere))
    adapters.decks.put_attempt(
        "talk",
        replace(_an_attempt(None, outcome=BuildOutcome.RUNNING), started_at=elsewhere),
    )
    deck = adapters.decks.get("talk")
    assert deck is not None
    assert deck.build is not None
    assert deck.attempt is not None
    read_back = (deck.changed_at, deck.build.built_at, deck.attempt.started_at)

    assert repr(read_back) == repr((_NOON, _NOON, _NOON))


@pytest.mark.parametrize("stored", ["../elsewhere", "a/b", ""])
def test_a_stored_run_that_is_no_single_name_under_the_root_fails_loud_on_read(
    postgres_stores: Adapters, engine: Engine, stored: str
) -> None:
    """A run is never read as a path a retention pass could delete elsewhere."""
    source = _a_source(postgres_stores, "mine", _ADMIN)
    postgres_stores.decks.put(_a_deck("talk", source))
    postgres_stores.decks.put_build("talk", _a_build("c"))
    with engine.begin() as connection:
        connection.execute(text("UPDATE decks SET built_run = :run"), {"run": stored})

    with pytest.raises(ValueError, match="builds root"):
        postgres_stores.decks.get("talk")


def test_a_build_an_older_release_moved_without_its_run_is_no_standing_build(
    postgres_stores: Adapters, engine: Engine
) -> None:
    """A talk written beside the run of an older build is never served as it."""
    source = _a_source(postgres_stores, "mine", _ADMIN)
    postgres_stores.decks.put(_a_deck("talk", source, commit="c2"))
    postgres_stores.decks.put_build("talk", _a_build("c1"))
    moved = _a_build("c2")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE decks SET active_build = :directory, pdf_export = :pdf,"
                " built_commit = :commit WHERE slug = 'talk'"
            ),
            {
                "directory": str(moved.directory),
                "pdf": str(moved.pdf),
                "commit": moved.commit,
            },
        )

    deck = postgres_stores.decks.get("talk")

    assert deck is not None
    assert deck.build is None


def test_a_postgres_deck_of_a_source_that_does_not_exist_is_an_error_not_a_refusal(
    postgres_stores: Adapters,
) -> None:
    source = _a_source(postgres_stores, "mine", _ADMIN)
    orphan = replace(_a_deck("talk", source), source_id="no-such-source")

    with pytest.raises(IntegrityError):
        postgres_stores.decks.put(orphan)


def test_postgres_preferences_of_an_account_that_does_not_exist_are_an_error(
    postgres_stores: Adapters,
) -> None:
    with pytest.raises(IntegrityError):
        postgres_stores.preferences.write(
            "nobody", PersonPreferences(theme=ThemeChoice.DARK)
        )
