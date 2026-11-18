"""A deck whose real build a route test serves, over the real build adapter."""

from datetime import timedelta
from pathlib import Path
from typing import Final

from gitmirror.model import CredentialReference
from presentator.adapters.build_cache import BuildCache
from presentator.adapters.builds import HostToolchain, SlidevBuilds
from presentator.adapters.decks import SourceMirrors
from presentator.contracts.decks import Build, BuildRun, Deck
from tests.presentator.application.support.fakes import THE_TOOLCHAIN, FakeDeckStore
from tests.support.lobby import (
    ADMIN,
    NOW,
    SOURCE_ID,
    GivenDecks,
    a_configured_source,
)
from tests.support.mirror_support import PinsInMemory
from tests.support.run_objects import A_CACHE_BOUND, DirectoryRunObjects

_A_COMMIT: Final = "a3f19c2b8d4e5f60718293a4b5c6d7e8f9012345"


class _NoCredentials:
    def resolve(self, reference: CredentialReference) -> str | None:
        return None


def real_builds_under(root: Path) -> SlidevBuilds:
    """The real adapter over a real directory; only what it reads is ever used."""
    return SlidevBuilds(
        cache=BuildCache(
            root=root,
            store=DirectoryRunObjects(root / "store"),
            bound_bytes=A_CACHE_BOUND,
        ),
        identity=THE_TOOLCHAIN,
        toolchain=HostToolchain(project=root, bound=timedelta(minutes=1)),
        mirrors=SourceMirrors(
            directory=root / "mirrors",
            credentials=_NoCredentials(),
            pull_timeout=timedelta(minutes=1),
            host_keys=PinsInMemory(),
        ),
        output_megabytes=1,
    )


def a_swapped_build(tmp_path: Path, *, slug: str) -> tuple[GivenDecks, Path]:
    """The deck side with one built deck, and the run directory a link may land in.

    The deck was adopted with plain files; each test then lays its link, which
    is what a deck's own code can do after the build was held.
    """
    run = tmp_path / "run" / "out"
    talk = run / "talk"
    (talk / "assets").mkdir(parents=True)
    (talk / "index.html").write_text("<h1>a talk</h1>", encoding="utf-8")
    (talk / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    pdf = run / "deck.pdf"
    pdf.write_bytes(b"%PDF-1.7 the real one")
    store = FakeDeckStore()
    store.put(
        Deck(
            slug=slug,
            title=slug,
            changed_at=NOW,
            owner_id=ADMIN,
            source_id=SOURCE_ID,
            commit=_A_COMMIT,
            build=None,
            attempt=None,
        ),
    )
    store.put_build(
        slug,
        Build(
            run=BuildRun("run"),
            commit=_A_COMMIT,
            built_at=NOW,
            toolchain=None,
        ),
    )
    given = GivenDecks(
        store=store,
        source=a_configured_source("git@heimserver:decks.git"),
        builder=real_builds_under(tmp_path),
    )
    return given, run
