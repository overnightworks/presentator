"""Decks of a source that names a folder, from a real repository to the deck rows."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from presentator.adapters.deck_store import SqliteDeckStore
from presentator.adapters.decks import (
    FilesystemLocalMount,
    SqliteSourceRunStore,
    SqliteSourceStore,
)
from presentator.application.build_retention import REMOVED_DECK_KEPT
from presentator.application.decks import (
    AddedSource,
    Decks,
    NewSourceDraft,
    SourceRefusal,
)
from presentator.contracts.decks import (
    MANIFEST_FILE,
    SLIDES_FILE,
    ConnectionCheckResult,
    SourceRunOutcome,
)
from tests.presentator.application.support.decks_support import (
    BUILD_BOUND,
    FINGERPRINT_KEY,
    HTTPS_URL,
    READ_ONLY,
    decks_over,
    having,
)
from tests.presentator.application.support.decks_support import (
    OWNER as FAKE_OWNER,
)
from tests.presentator.application.support.fakes import (
    FakeBuildRunner,
    FakeConnectionChecker,
    FakeDeployKeyDrafts,
    FakeToolchainThemes,
    FakeWebhookSecrets,
    FrozenClock,
)
from tests.support.deck_source_support import (
    OWNER,
    a_source_database,
    a_source_store,
    a_source_write,
    folders_under,
)
from tests.support.git_remote import GitRemote

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_LATER = _PUSHED_AT + timedelta(hours=1)


def a_deck_tree(under: str, slug: str, *, words: str = "one") -> dict[str, str]:
    """The files of one deck folder that a source takes in."""
    prefix = f"{under}/{slug}" if under else slug
    return {
        f"{prefix}/{MANIFEST_FILE}": f'title = "Deck {slug}"\n',
        f"{prefix}/{SLIDES_FILE}": f"# {words}\n",
    }


@dataclass(frozen=True, slots=True, kw_only=True)
class Wired:
    """The use cases and the real stores and mirror they run over."""

    decks: Decks
    sources: SqliteSourceStore
    store: SqliteDeckStore
    runs: SqliteSourceRunStore
    builder: FakeBuildRunner
    clock: FrozenClock
    mirrors: Path


def wired(tmp_path: Path) -> Wired:
    """The deck use cases over a real file and a real mirror, building nothing."""
    database = a_source_database(tmp_path)
    sources = a_source_store(database)
    store = SqliteDeckStore(database=database)
    runs = SqliteSourceRunStore(database=database)
    builder = FakeBuildRunner()
    clock = FrozenClock(instant=_LATER)
    decks = Decks(
        sources=sources,
        key_drafts=FakeDeployKeyDrafts(),
        folders=folders_under(tmp_path),
        store=store,
        builder=builder,
        source_runs=runs,
        checker=FakeConnectionChecker(),
        toolchain_themes=FakeToolchainThemes(),
        clock=clock,
        local_mount=FilesystemLocalMount(mount=tmp_path),
        webhook_secrets=FakeWebhookSecrets(),
        build_bound=BUILD_BOUND,
        fingerprint_key=FINGERPRINT_KEY,
    )
    return Wired(
        decks=decks,
        sources=sources,
        store=store,
        runs=runs,
        builder=builder,
        clock=clock,
        mirrors=tmp_path / "mirrors",
    )


def slugs(world: Wired) -> list[str]:
    return sorted(deck.slug for deck in world.store.all())


def added_with_folder(
    decks: Decks, url: str, folder: str, *, owner_id: str, secret: str = ""
) -> AddedSource | SourceRefusal:
    """Check the address without a folder, then create the source with one."""
    checked = decks.check_connection(
        url=url, secret=secret, owner_id=owner_id, key_draft_id=""
    )
    assert isinstance(checked, ConnectionCheckResult)
    return decks.add_source(
        NewSourceDraft(
            name="talks",
            url=url,
            secret=secret,
            fingerprint=checked.fingerprint or "",
            folder=folder,
        ),
        owner_id=owner_id,
    )


def test_a_push_outside_the_folder_rebuilds_no_deck(
    remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    world.sources.add(a_source_write(remote.url, folder="examples"))
    world.decks.refresh()
    remote.commit(a_deck_tree("", "elsewhere"), at=_LATER)

    world.decks.refresh()

    assert world.builder.built == ["a"]
    assert slugs(world) == ["a"]


def test_a_push_inside_one_deck_rebuilds_only_that_deck(
    remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(
        a_deck_tree("examples", "a") | a_deck_tree("examples", "b"), at=_PUSHED_AT
    )
    world.sources.add(a_source_write(remote.url, folder="examples"))
    world.decks.refresh()
    world.builder.built.clear()

    remote.commit(a_deck_tree("examples", "a", words="two"), at=_LATER)
    world.decks.refresh()

    assert world.builder.built == ["a"]


@pytest.mark.parametrize(
    "replacement", [{}, {"examples": "a file now\n"}], ids=["missing", "a file"]
)
def test_a_folder_that_goes_missing_ends_its_decks_and_they_return_when_it_does(
    remote: GitRemote, tmp_path: Path, replacement: dict[str, str]
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    stored = world.sources.add(a_source_write(remote.url, folder="examples"))
    assert stored is not None
    world.decks.refresh()

    remote.remove("examples", at=_LATER)
    if replacement:
        remote.commit(replacement, at=_LATER)
    world.decks.refresh()
    newest = world.runs.newest(stored.id)

    assert slugs(world) == []
    assert newest is not None
    assert newest.outcome is SourceRunOutcome.SUCCESS

    if replacement:
        (remote.work / "examples").unlink()
    remote.commit(a_deck_tree("examples", "a"), at=_LATER)
    world.decks.refresh()

    assert slugs(world) == ["a"]


def test_a_decks_row_is_deleted_only_once_its_folder_was_missing_past_the_expiry(
    remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    stored = world.sources.add(a_source_write(remote.url, folder="examples"))
    assert stored is not None
    world.decks.refresh()
    remote.remove("examples", at=_LATER)

    world.decks.refresh()
    kept = world.store.for_source(stored.id)
    world.clock.advance(REMOVED_DECK_KEPT + timedelta(days=1))
    world.decks.refresh()

    assert [deck.slug for deck in kept] == ["a"]
    assert world.store.for_source(stored.id) == ()


def test_the_first_source_keeps_a_deck_name_a_second_one_also_carries(
    remote: GitRemote, another_remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    another_remote.commit(a_deck_tree("", "a"), at=_PUSHED_AT)
    first = world.sources.add(
        a_source_write(remote.url, name="first", folder="examples")
    )
    world.sources.add(a_source_write(another_remote.url, name="second"))
    assert first is not None

    world.decks.refresh()

    kept = world.store.get("a")
    assert kept is not None
    assert kept.source_id == first.id


def test_removing_a_source_with_a_folder_takes_its_mirror_and_decks_away(
    remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    stored = world.sources.add(a_source_write(remote.url, folder="examples"))
    assert stored is not None
    world.decks.refresh()
    assert any(world.mirrors.iterdir())

    removed = world.decks.remove_source(
        stored.name, expected_deck_count=1, expected_run_count=1
    )

    assert removed is not None
    assert (world.sources.all(), world.store.for_source(stored.id)) == ((), ())
    assert not any(world.mirrors.iterdir())


def test_a_webhook_refresh_takes_in_the_decks_of_a_source_with_a_folder(
    remote: GitRemote, tmp_path: Path
) -> None:
    world = wired(tmp_path)
    remote.commit(a_deck_tree("examples", "a"), at=_PUSHED_AT)
    added = added_with_folder(world.decks, remote.url, "examples", owner_id=OWNER.id)
    assert isinstance(added, AddedSource)
    remote.commit(a_deck_tree("examples", "b"), at=_LATER)

    accepted = world.decks.accept_hook(added.source.name, added.webhook_secret)

    assert accepted
    assert slugs(world) == ["a", "b"]


@pytest.mark.parametrize("typed", ["examples/", " examples ", "examples"])
def test_a_typed_folder_is_stored_without_surrounding_space_or_a_trailing_slash(
    typed: str,
) -> None:
    store = having()
    decks = decks_over(sources=store)

    added = added_with_folder(
        decks, HTTPS_URL, typed, owner_id=FAKE_OWNER, secret=READ_ONLY
    )

    assert isinstance(added, AddedSource)
    assert [source.folder for source in store.all()] == ["examples"]


@pytest.mark.parametrize("typed", ["/", "a//b", "..", "/x"])
def test_a_malformed_folder_is_refused_and_nothing_is_stored(typed: str) -> None:
    store = having()
    decks = decks_over(sources=store)

    refused = added_with_folder(
        decks, HTTPS_URL, typed, owner_id=FAKE_OWNER, secret=READ_ONLY
    )

    assert refused is SourceRefusal.MALFORMED_FOLDER
    assert store.all() == ()


def test_a_create_with_a_folder_stores_it_on_a_check_made_without_one() -> None:
    store = having()
    decks = decks_over(sources=store)

    added = added_with_folder(
        decks, HTTPS_URL, "examples", owner_id=FAKE_OWNER, secret=READ_ONLY
    )

    assert isinstance(added, AddedSource)
    assert added.source.folder == "examples"
