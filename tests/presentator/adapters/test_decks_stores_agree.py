"""The SQLite and the Postgres stores answer the same to the same writes."""

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from presentator.contracts.decks import (
    RECENT_SOURCE_RUNS,
    Build,
    BuildAttempt,
    BuildFailure,
    BuildFailureReason,
    BuildOutcome,
    Deck,
    DeployKeyDraft,
    Source,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceWrite,
    ToolchainIdentity,
)
from presentator.contracts.preferences import (
    InstanceSettings,
    PersonPreferences,
    ThemeChoice,
)
from presentator.ports.decks import DeployKeyDrafts
from tests.presentator.adapters.support.source_stores_support import (
    ANOTHER_INSTANCE_KEY,
    Adapters,
    NumberedKeyPairs,
    postgres_adapters,
    sqlite_adapters,
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
def sqlite_stores(tmp_path: Path, numbered_keys: NumberedKeyPairs) -> Adapters:
    return sqlite_adapters(tmp_path, numbered_keys)


@pytest.fixture
def postgres_stores(
    database_url: str, numbered_keys: NumberedKeyPairs
) -> Iterator[Adapters]:
    with postgres_adapters(database_url, numbered_keys) as adapters:
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


def _a_run(source: Source, minutes: int, reason: SourceRunFailure | None) -> SourceRun:
    return SourceRun(
        source_id=source.id,
        at=_NOON + timedelta(minutes=minutes),
        outcome=SourceRunOutcome.SUCCESS
        if reason is None
        else SourceRunOutcome.FAILURE,
        commit=None if reason else f"commit-{minutes}",
        reason=reason,
    )


def _write_and_read_runs(adapters: Adapters) -> list[object]:
    """Every `SourceRuns` method, each observed by what it answers and leaves."""
    adapters.seed_owner(_ADMIN)
    polled = adapters.sources.add(_a_write("polled"))
    quiet = adapters.sources.add(_a_write("quiet"))
    assert polled is not None
    assert quiet is not None
    runs = adapters.runs
    observed: list[object] = [runs.newest(polled.id), runs.recent(polled.id)]

    for minutes, reason in (
        (0, None),
        (1, SourceRunFailure.REFUSED),
        (1, SourceRunFailure.CREDENTIAL_UNRESOLVABLE),
        (1, None),
        (2, SourceRunFailure.FAILED),
    ):
        runs.record(_a_run(polled, minutes, reason))
        observed.append(runs.newest(polled.id))
        observed.append(runs.recent(polled.id))
    assert len(runs.recent(polled.id)) == RECENT_SOURCE_RUNS
    runs.record(_a_run(quiet, 9, SourceRunFailure.UNREACHABLE))
    observed.append(runs.recent(quiet.id))

    runs.remove_for_source(polled.id)
    runs.remove_for_source("no-such-source")
    observed.extend((runs.newest(polled.id), runs.recent(polled.id)))
    observed.append(runs.recent(quiet.id))
    return observed


def test_a_run_recorded_at_another_offset_is_the_same_instant_through_both_adapters(
    sqlite_stores: Adapters, postgres_stores: Adapters
) -> None:
    at = _NOON.astimezone(_TWO_HOURS_EAST)
    read_back: list[datetime] = []
    for adapters in (sqlite_stores, postgres_stores):
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
        read_back.append(newest.at)

    assert read_back == [_NOON, _NOON]


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


def _put_and_read(adapters: Adapters) -> list[object]:
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


def _build_and_attempt(adapters: Adapters) -> list[object]:
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


def _marked_and_removed(adapters: Adapters) -> list[object]:
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


def _write_and_read_settings(adapters: Adapters) -> list[object]:
    """Every `InstanceSettingsStore` method, each observed by what it answers."""
    settings = adapters.settings
    observed: list[object] = [settings.read()]
    for name in ("Heim é", "", "A name with 'quotes' and \"more\""):
        settings.write(InstanceSettings(name=name))
        observed.append(settings.read())
    return observed


def _write_and_read_preferences(adapters: Adapters) -> list[object]:
    """Every `PersonPreferencesStore` method, each observed by what it answers."""
    preferences = adapters.preferences
    for person in (_PERSON, _OTHER_PERSON):
        adapters.seed_owner(person)
    observed: list[object] = [preferences.read(_PERSON), preferences.delete(_PERSON)]
    for theme in ThemeChoice:
        preferences.write(_PERSON, PersonPreferences(theme=theme))
        observed.append(preferences.read(_PERSON))
    preferences.write(_OTHER_PERSON, PersonPreferences(theme=ThemeChoice.DARK))
    observed.extend(
        [
            preferences.delete(_PERSON),
            preferences.delete(_PERSON),
            preferences.read(_PERSON),
            preferences.read(_OTHER_PERSON),
        ]
    )
    return observed


@pytest.mark.parametrize(
    "scenario",
    [
        _write_and_read_drafts,
        _write_and_read_sources,
        _write_and_read_runs,
        _put_and_read,
        _build_and_attempt,
        _marked_and_removed,
        _write_and_read_settings,
        _write_and_read_preferences,
    ],
)
def test_the_same_writes_read_the_same_through_both_adapters(
    scenario: Callable[[Adapters], list[object]],
    sqlite_stores: Adapters,
    postgres_stores: Adapters,
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
    sqlite_stores: Adapters, postgres_stores: Adapters
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
