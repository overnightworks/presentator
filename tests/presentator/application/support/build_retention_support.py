"""Rows, builds and disks a retention test starts from."""

from datetime import UTC, datetime

from presentator.contracts.decks import Build, BuildRun, Deck, Source
from tests.presentator.application.support.decks_support import a_source, having
from tests.presentator.application.support.fakes import (
    THE_TOOLCHAIN,
    FakeBuildRunner,
    FakeDeckFolders,
    FakeDeckStore,
    FakeSourceStore,
)

THE_SOURCE = a_source("decks", identifier="the-configured-source")
THE_MOMENT = datetime(2026, 1, 15, 9, tzinfo=UTC)
THE_OWNER = "the account that set the instance up"
THE_COMMIT = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


def a_row(
    slug: str, *, source: Source = THE_SOURCE, build: Build | None = None
) -> Deck:
    return Deck(
        slug=slug,
        title="A talk",
        changed_at=THE_MOMENT,
        owner_id=THE_OWNER,
        source_id=source.id,
        commit=THE_COMMIT,
        build=build,
        attempt=None,
    )


def a_build_in(run: str) -> Build:
    return Build(
        run=BuildRun(run),
        commit=THE_COMMIT,
        built_at=THE_MOMENT,
        toolchain=THE_TOOLCHAIN,
    )


def runs_left(builder: FakeBuildRunner) -> set[str]:
    return {run.name for run in builder.on_disk}


def laid_down(builder: FakeBuildRunner, *names: str) -> None:
    builder.on_disk.update(BuildRun(name) for name in names)


def over_rows(
    *rows: Deck,
    on_disk: tuple[str, ...],
    sources: tuple[Source, ...] = (THE_SOURCE,),
) -> tuple[FakeBuildRunner, FakeDeckStore, FakeDeckFolders, FakeSourceStore]:
    builder = FakeBuildRunner()
    laid_down(builder, *on_disk)
    store = FakeDeckStore()
    for row in rows:
        store.kept[row.slug] = row
    mirror = FakeDeckFolders(carried={source.id: () for source in sources})
    return builder, store, mirror, having(*sources)
