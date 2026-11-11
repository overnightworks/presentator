"""A toolchain stand-in on PATH and the build adapter over it, for the build tests."""

from datetime import timedelta
from pathlib import Path

import pytest

from presentator.adapters.builds import DeckToolchain, HostToolchain, SlidevBuilds
from presentator.adapters.decks import FilesystemLocalMount, SourceMirrors
from presentator.contracts.decks import (
    Artefacts,
    BuildFailure,
    Source,
    ToolchainIdentity,
    ToolchainUnavailable,
)
from tests.presentator.adapters.support.credentials_support import OpenCredentials
from tests.presentator.application.support.fakes import THE_TOOLCHAIN
from tests.support.git_remote import with_the_program

A_GENEROUS_BOUND = timedelta(seconds=30)

# Bigger than the few bytes a stand-in writes, so only the test about the bound
# ever meets it.
A_TALK_THIS_BIG_IS_FINE = 64

TOOLCHAIN_PROGRAM = "pnpm"


FINDS_ITS_OUTPUT = """#!/bin/sh
set -eu
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *) shift;;
    esac
done
"""


A_TOOLCHAIN_THAT_BUILDS = (
    FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *) mkdir -p "$out"; printf 'a talk' > "$out/index.html";;
esac
"""
)


def on_this_machine(
    tmp_path: Path,
    *,
    bound: timedelta = A_GENEROUS_BOUND,
) -> HostToolchain:
    """The toolchain a development run builds with: a project on this machine."""
    project = tmp_path / "slidev"
    project.mkdir(exist_ok=True)
    return HostToolchain(project=project, bound=bound)


def builds_under(
    tmp_path: Path,
    *,
    toolchain: DeckToolchain,
    identity: ToolchainIdentity = THE_TOOLCHAIN,
    output_megabytes: int = A_TALK_THIS_BIG_IS_FINE,
    local_mount: FilesystemLocalMount | None = None,
) -> SlidevBuilds:
    return SlidevBuilds(
        builds=tmp_path / "builds",
        identity=identity,
        toolchain=toolchain,
        mirrors=SourceMirrors(
            directory=tmp_path / "mirrors",
            credentials=OpenCredentials(),
            pull_timeout=A_GENEROUS_BOUND,
            local_mount=(
                FilesystemLocalMount(mount=tmp_path)
                if local_mount is None
                else local_mount
            ),
        ),
        output_megabytes=output_megabytes,
    )


def builds_ready_for(
    tmp_path: Path,
    source: Source,
    *,
    toolchain: DeckToolchain | None = None,
    output_megabytes: int = A_TALK_THIS_BIG_IS_FINE,
) -> SlidevBuilds:
    """The adapter with the source already mirrored, as a take-in leaves it."""
    builds = builds_under(
        tmp_path,
        toolchain=toolchain if toolchain is not None else on_this_machine(tmp_path),
        output_megabytes=output_megabytes,
    )
    mirror = builds.mirrors.of(source)
    assert mirror is not None
    assert mirror.connect().revision is not None
    return builds


def what_it_built(
    built: Artefacts | BuildFailure | ToolchainUnavailable,
) -> Artefacts:
    """What the build left, for a test that has already said it expects a talk."""
    assert isinstance(built, Artefacts)
    return built


def with_the_toolchain(
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
) -> None:
    """Put a `pnpm` this test wrote on PATH, for a build on this machine."""
    with_the_program(machine, tmp_path, TOOLCHAIN_PROGRAM, script)
