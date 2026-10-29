"""Building a deck with a toolchain on PATH, against a real bare repository.

The toolchain here is a script this test wrote, not Slidev: what belongs to
this adapter is which command it runs, in which environment, under which bound,
and where the result is allowed to stand. That Slidev itself builds a deck, and
that the worker really denies it this machine, is proven by driving the real
interface.
"""

import os
import shutil
import stat
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from errno import ELOOP
from pathlib import Path

import pytest

from presentator.adapters import builds as builds_module
from presentator.adapters.build_wire import MAXIMUM_ENTRIES
from presentator.adapters.builds import (
    DeckToolchain,
    HostToolchain,
    SlidevBuilds,
)
from presentator.adapters.decks import FilesystemLocalMount, SourceMirrors
from presentator.contracts.decks import (
    FAILURE_TEXT_LIMIT,
    OUTPUT_DIRECTORY,
    Artefacts,
    BuildFailure,
    Deck,
    Source,
    talk_address,
)
from tests.presentator.adapters.support.credentials_support import OpenCredentials
from tests.support.git_remote import (
    EXAMPLE_SLUG,
    MAIN_BRANCH,
    GitRemote,
    a_path_carrying,
    with_the_program,
)

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_A_GENEROUS_BOUND = timedelta(seconds=30)
# Bigger than the few bytes a stand-in writes, so only the test about the bound
# ever meets it.
_A_TALK_THIS_BIG_IS_FINE = 64
_ONLY_A_MEGABYTE_MAY_BE_LEFT = 1
_MEGABYTES_A_GREEDY_BUILD_WRITES = 3
_WATCHED_OFTEN = timedelta(milliseconds=50)
_CLOSED_TO_EVERYONE = 0o000
_TALK = "talk"
_PDF_FILE = "deck.pdf"
_A_DATABASE_FILE = "presentator.sqlite3"
_A_SLUG_OF_SPACES = "a deck of spaces"
# What a command line may carry beside a space: nothing here reads any of it as
# grammar, so nothing here may lose it.
_ARGUMENTS_OF_EVERY_KIND = (
    "--flag=a value",
    'quoted "like this"',
    "--network=none --cap-add=ALL",
)
_NO_BUDGET_AT_ALL = timedelta(0)
_OWNER = "the-admin"
_SOURCE_ID = "the-source-that-carried-it"
_A_SERVER_SECRET = "PRESENTATOR_" + "A_VALUE_ONLY_THIS_TEST_SET"
_WHAT_THE_SERVER_HOLDS = "the words only this test set in the environment"
# What the toolchain stand-in writes down, so a test can read the command line
# and the environment back.
_RECORDED_COMMAND = "command"
_RECORDED_ENVIRONMENT = "environment"
_A_BUILT_PAGE = "index.html"
_TOOLCHAIN_PROGRAM = "pnpm"
_WHAT_A_TALK_SAYS = "a talk"


_FINDS_ITS_OUTPUT = """#!/bin/sh
set -eu
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *) shift;;
    esac
done
"""


_A_TOOLCHAIN_THAT_BUILDS = (
    _FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *) mkdir -p "$out"; printf 'a talk' > "$out/index.html";;
esac
"""
)
# Builds as above, then its export step turns one name of what was written
# into a link, the way a deck's own `vite.config.ts` could.
_A_TOOLCHAIN_THAT_PLANTS_A_LINK = (
    _A_TOOLCHAIN_THAT_BUILDS
    + """case "$out" in
    *.pdf) cd "$(dirname "$out")"; {planted};;
esac
"""
)
# Where to write is baked in rather than read from the environment, because
# the environment a build runs in is exactly what this stand-in is here to show.
# Every argument is kept whole, ended by a nul, and every command by a record
# separator: a stand-in that joined them with spaces would read two arguments
# and one argument carrying a space as the same thing.
_A_TOOLCHAIN_THAT_RECORDS = """#!/bin/sh
set -eu
for given in "$@"; do
    printf '%s\\000' "$given" >> "{recorded}/command"
done
printf '\\036' >> "{recorded}/command"
env | sort >> "{recorded}/environment"
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *) shift;;
    esac
done
case "$out" in
    "") ;;
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *) mkdir -p "$out"; printf 'a talk' > "$out/index.html";;
esac
"""
_A_TOOLCHAIN_THAT_FAILS = """#!/bin/sh
echo "the deck does not build" >&2
exit 1
"""
_WHAT_A_FAILING_TOOLCHAIN_SAID = "the deck does not build"
# A deck's own toolchain can print megabytes; what a page shows is the end of
# it, where the reason stands, and that end is all this server ever holds.
_LAST_WORDS = "the last words of the toolchain"
_A_TOOLCHAIN_THAT_NEVER_STOPS_TALKING = f"""#!/bin/sh
i=0
while [ $i -lt 32 ]; do
    dd if=/dev/zero bs=65536 count=8 2>/dev/null | tr '\\0' 'x' >&2
    i=$((i+1))
done
echo "{_LAST_WORDS}" >&2
exit 1
"""
_A_TOOLCHAIN_THAT_NEVER_ENDS = """#!/bin/sh
sleep 60
"""
# One that says everything it has to say and then hangs anyway, the way an
# export whose browser never comes back does.
_A_TOOLCHAIN_THAT_STOPS_TALKING_BUT_NOT_RUNNING = """#!/bin/sh
exec 2>&-
sleep 60
"""
# A talk of the size a test asks for, and a step that goes on running after it
# wrote it: a build is stopped while it writes, not once it is done.
_A_TOOLCHAIN_THAT_WRITES = (
    _FINDS_ITS_OUTPUT
    + """a_megabyte=1048576
case "$out" in
    *.pdf)
        dd if=/dev/zero of="$out" bs=$a_megabyte count={pdf} 2>/dev/null
        {and_then}
        ;;
    *)
        mkdir -p "$out"
        dd if=/dev/zero of="$out/index.html" bs=$a_megabyte count={talk} 2>/dev/null
        ;;
esac
"""
)
_AND_THEN_IT_KEEPS_GOING = "sleep 60"
_AND_THEN_IT_IS_DONE = ":"
# A build that leaves a link where a file would be, pointing at something of
# this machine's that is neither its to read nor its to have counted.
_A_TOOLCHAIN_THAT_LEAVES_A_LINK_OUT = (
    _FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *)
        mkdir -p "$out"
        printf 'a talk' > "$out/index.html"
        ln -s "{outside}" "$out/a-way-out"
        ;;
esac
"""
)
_A_TOOLCHAIN_THAT_LEAVES_A_LINK_OUT_AND_FAILS = (
    _FINDS_ITS_OUTPUT
    + """mkdir -p "$out"
ln -s "{outside}" "$out/a-way-out"
echo "the deck does not build" >&2
exit 1
"""
)
# A deck's own code writes in the directory of its run, and may close a
# directory behind it that the run's leftovers then have to be opened out of.
# A development run gives a deck this server's own rights, so its build can
# close the very root every build stands in.
_A_TOOLCHAIN_THAT_CLOSES_THE_ROOT = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *) shift;;
    esac
done
mkdir -p "$out"
chmod 500 "$(dirname "$(dirname "$(dirname "$out")")")"
echo "the deck does not build" >&2
exit 1
"""
# One entry over the cap once the folder holding them is counted, two of them
# directories: whatever order they are read in, the count stops with a
# directory still waiting to be opened.
_A_TOOLCHAIN_THAT_BUILDS_MORE_FILES_THAN_ALLOWED = (
    _FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *)
        mkdir -p "$out/first" "$out/second"
        seq 1 {files} | (cd "$out" && xargs touch)
        ;;
esac
"""
)
_A_TOOLCHAIN_THAT_TAKES_ITS_OWN_TALK_AWAY = (
    _FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out"; rm -rf "$(dirname "$out")"; sleep 60;;
    *) mkdir -p "$out"; printf 'a talk' > "$out/index.html";;
esac
"""
)
_A_TOOLCHAIN_THAT_TAKES_THE_WHOLE_RUN_AWAY = (
    _FINDS_ITS_OUTPUT
    + """rm -rf "$(dirname "$(dirname "$out")")"
echo "the deck does not build" >&2
exit 1
"""
)
_A_TOOLCHAIN_THAT_CLOSES_WHAT_IT_BUILT = (
    _FINDS_ITS_OUTPUT
    + """case "$out" in
    *.pdf) printf '%%PDF-1.7' > "$out";;
    *)
        mkdir -p "$out/closed"
        printf 'a talk' > "$out/index.html"
        chmod 000 "$out/closed"
        ;;
esac
"""
)
_A_TOOLCHAIN_THAT_CLOSES_WHAT_IT_WROTE = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do
    case "$1" in
        --out|--output) out="$2"; shift 2;;
        *) shift;;
    esac
done
mkdir -p "$out/closed"
chmod 000 "$out/closed"
echo "the deck does not build" >&2
exit 1
"""

# A background child of its own, the way slidev export's own Chromium is a
# child of slidev rather than of pnpm: only a kill of the whole group reaches
# it, so the pid it wrote down is this test's proof.
_A_TOOLCHAIN_THAT_LEAVES_A_CHILD_RUNNING = """#!/bin/sh
sleep 60 &
echo $! > "{pid_file}"
sleep 60
"""
_A_BOUND_A_SHELL_STARTS_WELL_WITHIN = timedelta(milliseconds=300)
_GRANDCHILD_GONE_WITHIN = timedelta(seconds=5)
_POLL_EVERY = timedelta(milliseconds=10)


def a_source(remote: GitRemote) -> Source:
    """The one source, carrying the example deck at its newest commit."""
    remote.commit_example_deck(at=_PUSHED_AT)
    return Source(
        id=_SOURCE_ID,
        name="decks",
        url=remote.url,
        ref=MAIN_BRANCH,
        owner_id=_OWNER,
    )


@pytest.fixture
def source(remote: GitRemote) -> Source:
    """That source, for the tests that only ever want the one."""
    return a_source(remote)


def a_deck(remote: GitRemote, *, slug: str = EXAMPLE_SLUG) -> Deck:
    return Deck(
        slug=slug,
        title="Hello Co-Presenter",
        changed_at=_PUSHED_AT,
        owner_id=_OWNER,
        source_id=_SOURCE_ID,
        commit=remote.head,
        build=None,
        attempt=None,
    )


def on_this_machine(
    tmp_path: Path,
    *,
    bound: timedelta = _A_GENEROUS_BOUND,
) -> HostToolchain:
    """The toolchain a development run builds with: a project on this machine."""
    project = tmp_path / "slidev"
    project.mkdir(exist_ok=True)
    return HostToolchain(project=project, bound=bound)


def builds_under(
    tmp_path: Path,
    *,
    toolchain: DeckToolchain,
    output_megabytes: int = _A_TALK_THIS_BIG_IS_FINE,
    local_mount: FilesystemLocalMount | None = None,
) -> SlidevBuilds:
    return SlidevBuilds(
        builds=tmp_path / "builds",
        toolchain=toolchain,
        mirrors=SourceMirrors(
            directory=tmp_path / "mirrors",
            credentials=OpenCredentials(),
            pull_timeout=_A_GENEROUS_BOUND,
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
    output_megabytes: int = _A_TALK_THIS_BIG_IS_FINE,
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


def what_it_built(built: Artefacts | BuildFailure) -> Artefacts:
    """What the build left, for a test that has already said it expects a talk."""
    assert isinstance(built, Artefacts)
    return built


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A machine whose environment holds a secret no build may ever see."""
    monkeypatch.setenv(_A_SERVER_SECRET, _WHAT_THE_SERVER_HOLDS)
    return monkeypatch


def with_the_toolchain(
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    script: str,
) -> None:
    """Put a `pnpm` this test wrote on PATH, for a build on this machine."""
    with_the_program(machine, tmp_path, _TOOLCHAIN_PROGRAM, script)


def recording(
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    program: str = _TOOLCHAIN_PROGRAM,
) -> Path:
    """A program that writes down the command line and environment it got."""
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    with_the_program(
        machine,
        tmp_path,
        program,
        _A_TOOLCHAIN_THAT_RECORDS.format(recorded=recorded),
    )
    return recorded


def test_a_decks_folder_at_its_commit_is_built_into_a_talk_and_a_pdf(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    artefacts = what_it_built(builds.build(a_deck(remote), source=source))

    assert builds.holds(artefacts)
    assert (artefacts.directory / _A_BUILT_PAGE).read_text(
        encoding="utf-8",
    ) == _WHAT_A_TALK_SAYS
    assert artefacts.pdf.read_bytes().startswith(b"%PDF")
    assert artefacts.directory.is_relative_to(tmp_path / "builds")


def test_a_builds_run_directory_lets_others_read_and_enter_it(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """#529 sentence 1: a build's run directory lets others read and enter it."""
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    artefacts = what_it_built(builds.build(a_deck(remote), source=source))

    run_directory = artefacts.directory.parents[1]
    others = stat.S_IMODE(run_directory.stat().st_mode) & stat.S_IRWXO
    assert others == stat.S_IROTH | stat.S_IXOTH


def test_a_build_of_a_source_that_left_its_mount_is_not_attempted(
    remote: GitRemote,
    source: Source,
    tmp_path: Path,
) -> None:
    """A file-kind source that left its mount is unreadable at build time too.

    Moved, deleted, or swapped for a symlink since it was fetched, no git
    process is asked to open wherever the address now points — the same
    refusal a poll makes.
    """
    builds = builds_under(
        tmp_path,
        toolchain=on_this_machine(tmp_path),
        local_mount=FilesystemLocalMount(mount=tmp_path / "elsewhere"),
    )

    built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure(text=None)


def test_a_second_build_of_the_same_deck_leaves_the_first_one_where_it_stands(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    first = what_it_built(builds.build(a_deck(remote), source=source))
    second = what_it_built(builds.build(a_deck(remote), source=source))

    assert first.directory != second.directory
    assert builds.holds(first)
    assert builds.holds(second)


def test_the_talk_is_built_against_the_address_it_is_delivered_under(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorded = recording(machine, tmp_path)

    builds_ready_for(tmp_path, source).build(a_deck(remote), source=source)

    built, exported = _every_command_in(recorded)
    assert built[:3] == ["exec", "slidev", "build"]
    assert built[built.index("--base") + 1] == talk_address(EXAMPLE_SLUG)
    assert exported[:3] == ["exec", "slidev", "export"]


def _every_command_in(recorded: Path) -> list[list[str]]:
    """Every command the stand-in was given, argument by argument as it got it."""
    written = (recorded / _RECORDED_COMMAND).read_bytes()
    return [
        [given.decode() for given in one_command.split(b"\0")[:-1]]
        for one_command in written.split(b"\036")[:-1]
    ]


def test_a_folder_name_carrying_spaces_stays_one_argument_of_its_own(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # What a deck is delivered under is its folder's name, whatever is in it,
    # and it reaches the toolchain as the one argument it is.
    remote.commit_example_deck(at=_PUSHED_AT, into=_A_SLUG_OF_SPACES)
    recorded = recording(machine, tmp_path)

    builds_ready_for(tmp_path, source).build(
        a_deck(remote, slug=_A_SLUG_OF_SPACES),
        source=source,
    )

    built, _ = _every_command_in(recorded)
    assert built[built.index("--base") + 1] == talk_address(_A_SLUG_OF_SPACES)


def test_what_a_stand_in_writes_down_is_every_argument_as_it_got_it(
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # The command lines above are only worth what this recording is worth: two
    # arguments joined into one, or one carrying a space read as two, would
    # otherwise pass for the same thing.
    recorded = recording(machine, tmp_path)

    subprocess.run([_TOOLCHAIN_PROGRAM, *_ARGUMENTS_OF_EVERY_KIND], check=True)

    assert _every_command_in(recorded) == [list(_ARGUMENTS_OF_EVERY_KIND)]


def test_a_build_carries_nothing_of_the_environment_this_server_runs_in(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    recorded = recording(machine, tmp_path)

    builds_ready_for(tmp_path, source).build(a_deck(remote), source=source)

    carried = (recorded / _RECORDED_ENVIRONMENT).read_text(encoding="utf-8")
    assert _A_SERVER_SECRET not in carried
    assert _WHAT_THE_SERVER_HOLDS not in carried
    assert "PATH=" in carried


@pytest.mark.parametrize(
    "toolchain",
    [
        (_A_TOOLCHAIN_THAT_FAILS, _A_GENEROUS_BOUND, _WHAT_A_FAILING_TOOLCHAIN_SAID),
        (_A_TOOLCHAIN_THAT_NEVER_ENDS, _NO_BUDGET_AT_ALL, None),
    ],
    ids=["a deck that does not build", "a build that runs past its bound"],
)
def test_a_build_that_did_not_finish_says_why_and_leaves_nothing_to_point_at(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    toolchain: tuple[str, timedelta, str | None],
) -> None:
    script, bound, said = toolchain
    with_the_toolchain(machine, tmp_path, script)

    builds = builds_ready_for(
        tmp_path, source, toolchain=on_this_machine(tmp_path, bound=bound)
    )
    built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure(text=said)
    assert list(builds.builds.iterdir()) == []


def test_a_step_that_stopped_talking_but_not_running_is_given_up_on_too(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(
        machine, tmp_path, _A_TOOLCHAIN_THAT_STOPS_TALKING_BUT_NOT_RUNNING
    )
    builds = builds_ready_for(
        tmp_path,
        source,
        toolchain=on_this_machine(tmp_path, bound=_A_BOUND_A_SHELL_STARTS_WELL_WITHIN),
    )

    built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure(text=None)
    assert list(builds.builds.iterdir()) == []


def test_a_toolchain_that_printed_without_end_hands_back_its_last_words(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_NEVER_STOPS_TALKING)

    built = builds_ready_for(tmp_path, source).build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    assert built.text is not None
    assert len(built.text) == FAILURE_TEXT_LIMIT
    assert built.text.endswith(_LAST_WORDS)


def test_a_build_past_its_bound_takes_the_whole_toolchain_tree_down_with_it(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "grandchild.pid"
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_LEAVES_A_CHILD_RUNNING.format(pid_file=pid_file),
    )
    builds = builds_ready_for(
        tmp_path,
        source,
        toolchain=on_this_machine(tmp_path, bound=_A_BOUND_A_SHELL_STARTS_WELL_WITHIN),
    )

    built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure(text=None)
    grandchild = int(pid_file.read_text(encoding="utf-8"))
    assert _gone_within(grandchild, deadline=_GRANDCHILD_GONE_WITHIN)


def _gone_within(pid: int, *, deadline: timedelta) -> bool:
    """Poll for the process to disappear, rather than trust a fixed wait."""
    ends_by = time.monotonic() + deadline.total_seconds()
    while time.monotonic() < ends_by:
        if not _is_alive(pid):
            return True
        time.sleep(_POLL_EVERY.total_seconds())
    return not _is_alive(pid)


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_without_a_toolchain_on_the_machine_there_is_no_build(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    builds = builds_ready_for(tmp_path, source)
    deck = a_deck(remote)
    machine.setenv("PATH", a_path_carrying())

    built = builds.build(deck, source=source)

    # What the operating system says names paths of this host, so it stays in
    # the log; the page is told that this build left no words.
    assert built == BuildFailure(text=None)
    assert EXAMPLE_SLUG in caplog.text
    assert _TOOLCHAIN_PROGRAM in caplog.text


def test_a_commit_the_mirror_cannot_read_the_deck_at_builds_nothing(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)

    built = builds_ready_for(tmp_path, source).build(
        a_deck(remote, slug="never-pushed"),
        source=source,
    )

    # A tree this host could not read out of its own mirror is nothing a deck's
    # author could act on, so the page is told nothing and the log holds it.
    assert built == BuildFailure(text=None)
    assert "never-pushed" in caplog.text


_A_REAL_TALK_UNDER_THE_ROOT = Path("builds/a-run/real-out/talk")


@pytest.mark.parametrize(
    ("talk", "pdf"),
    [
        (Path("outside"), Path("outside/deck.pdf")),
        (Path("builds/through-a-link"), Path("builds/through-a-link/deck.pdf")),
        (Path("builds/a-run/out/talk"), Path("builds/a-run/out/talk/deck.pdf")),
        (Path("builds/../outside"), Path("builds/../outside/deck.pdf")),
        (_A_REAL_TALK_UNDER_THE_ROOT, Path("builds")),
    ],
    ids=[
        "a place outside the root",
        "a link out of the root",
        "a run whose out is a link inside the root",
        "a way up out of the root",
        "the root itself as the PDF",
    ],
)
def test_artefacts_that_do_not_stand_under_the_root_are_not_held(
    tmp_path: Path,
    talk: Path,
    pdf: Path,
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    builds.builds.mkdir(parents=True)
    outside = a_written_talk(tmp_path / "outside")
    (builds.builds / "through-a-link").symlink_to(outside)
    run = builds.builds / "a-run"
    a_written_talk(tmp_path / _A_REAL_TALK_UNDER_THE_ROOT)
    (run / OUTPUT_DIRECTORY).symlink_to("real-out")

    assert not builds.holds(Artefacts(directory=tmp_path / talk, pdf=tmp_path / pdf))


def a_written_talk(directory: Path) -> Path:
    """A talk's page and a PDF beside it, as real files in that directory."""
    directory.mkdir(parents=True)
    (directory / _A_BUILT_PAGE).write_text(_WHAT_A_TALK_SAYS, encoding="utf-8")
    (directory / _PDF_FILE).write_bytes(b"%PDF-1.7")
    return directory


@pytest.mark.parametrize(
    "planted",
    [
        "rm deck.pdf; ln -s {elsewhere} deck.pdf",
        "mv deck.pdf a-real.pdf; ln -s a-real.pdf deck.pdf",
        "mv talk a-real-talk; ln -s a-real-talk talk",
        "ln -sf {elsewhere} talk/index.html",
        "rm deck.pdf; mkdir deck.pdf",
    ],
    ids=[
        "the PDF as a link out of the root",
        "the PDF as a link inside the root",
        "the talk as a link inside the root",
        "the page as a link out of the root",
        "the PDF as a directory",
    ],
)
def test_a_build_that_leaves_a_link_on_the_way_to_an_artefact_is_not_held(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    planted: str,
) -> None:
    """A deck's own build may write links; none of them is ever adopted.

    The link is planted by the build's last step, after everything it was
    asked for was written, so all that refuses the build is the link itself.
    """
    elsewhere = tmp_path / "presentator.sqlite3"
    elsewhere.write_bytes(b"what this server keeps to itself")
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_PLANTS_A_LINK.replace(
            "{planted}",
            planted.format(elsewhere=elsewhere),
        ),
    )
    builds = builds_ready_for(tmp_path, source)

    artefacts = what_it_built(builds.build(a_deck(remote), source=source))

    assert not builds.holds(artefacts)


def test_a_file_of_a_build_is_read_as_it_was_written(tmp_path: Path) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)

    file = builds.read(run / _TALK / "assets" / "app.js")

    assert file is not None
    with file:
        assert file.read() == b"console.log(1)"


@pytest.mark.parametrize(
    ("replaced", "read"),
    [
        (_PDF_FILE, _PDF_FILE),
        ("talk/assets/app.js", "talk/assets/app.js"),
        ("talk/assets", "talk/assets/app.js"),
        ("talk", "talk/index.html"),
    ],
    ids=["the PDF", "an asset", "a directory on the way", "the talk itself"],
)
def test_a_file_replaced_by_a_link_after_the_build_was_held_is_not_read(
    tmp_path: Path,
    replaced: str,
    read: str,
) -> None:
    """The link comes after `holds` said yes, which is the gap `read` closes."""
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)
    assert builds.holds(Artefacts(directory=run / _TALK, pdf=run / _PDF_FILE))
    secret = a_secret_beside_the_root(tmp_path)
    target = run / replaced
    if target.is_dir():
        shutil.rmtree(target)
        target.symlink_to(secret.parent)
    else:
        target.unlink()
        target.symlink_to(secret)

    assert builds.read(run / read) is None


@pytest.mark.parametrize("nothing_there", ["no-such-file", "talk"])
def test_a_name_that_is_no_file_of_a_build_is_not_read(
    tmp_path: Path,
    nothing_there: str,
) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    run = a_run_with_an_asset(builds)

    assert builds.read(run / nothing_there) is None


def test_a_path_that_leaves_the_root_is_not_read(tmp_path: Path) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    builds.builds.mkdir(parents=True)

    assert builds.read(a_secret_beside_the_root(tmp_path)) is None
    assert builds.read(builds.builds / ".." / _A_DATABASE_FILE) is None


def a_secret_beside_the_root(tmp_path: Path) -> Path:
    """Something this server keeps to itself, named like every file a build has."""
    secret = tmp_path / "secret" / _A_DATABASE_FILE
    secret.parent.mkdir(exist_ok=True)
    secret.write_bytes(b"what this server keeps to itself")
    (secret.parent / "app.js").write_bytes(secret.read_bytes())
    (secret.parent / _A_BUILT_PAGE).write_bytes(secret.read_bytes())
    return secret


def a_run_with_an_asset(builds: SlidevBuilds) -> Path:
    """A run holding a talk with one asset, and its PDF, as real files."""
    run = builds.builds / "a-run"
    talk = a_written_talk(run / _TALK)
    (run / _PDF_FILE).write_bytes((talk / _PDF_FILE).read_bytes())
    (talk / _PDF_FILE).unlink()
    (talk / "assets").mkdir()
    (talk / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    return run


def test_artefacts_that_were_never_written_are_not_held(tmp_path: Path) -> None:
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    never_written = builds.builds / "a-run-that-never-was" / _TALK

    assert not builds.holds(
        Artefacts(directory=never_written, pdf=never_written / "deck.pdf"),
    )


def test_a_talk_larger_than_a_build_may_leave_is_refused_with_that_reason(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Nothing is written until the last step, so no watch sees it: what the
    # build left is added up once more when it is over.
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_WRITES.format(
            talk=0,
            pdf=_MEGABYTES_A_GREEDY_BUILD_WRITES,
            and_then=_AND_THEN_IT_IS_DONE,
        ),
    )
    builds = builds_ready_for(
        tmp_path,
        source,
        output_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT,
    )

    built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    assert built.text is not None
    assert f"{_MEGABYTES_A_GREEDY_BUILD_WRITES} MB" in built.text
    assert f"at most {_ONLY_A_MEGABYTE_MAY_BE_LEFT} MB" in built.text
    assert list(builds.builds.iterdir()) == []


def test_a_build_that_has_written_too_much_is_stopped_where_it_stands(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # A machine is filled while a build runs, so the step that is still
    # running when it happens does not get to finish.
    machine.setattr(builds_module, "_ACCOUNTED_EVERY", _WATCHED_OFTEN)
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_WRITES.format(
            talk=_MEGABYTES_A_GREEDY_BUILD_WRITES,
            pdf=0,
            and_then=_AND_THEN_IT_KEEPS_GOING,
        ),
    )
    builds = builds_ready_for(
        tmp_path,
        source,
        output_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT,
    )

    began = time.monotonic()
    built = builds.build(a_deck(remote), source=source)

    assert time.monotonic() - began < _A_GENEROUS_BOUND.total_seconds()
    assert isinstance(built, BuildFailure)
    assert built.text is not None
    assert f"{_MEGABYTES_A_GREEDY_BUILD_WRITES} MB" in built.text
    assert list(builds.builds.iterdir()) == []


def test_a_talk_of_more_files_than_a_build_may_leave_is_refused_too(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_BUILDS_MORE_FILES_THAN_ALLOWED.format(
            files=MAXIMUM_ENTRIES - 2
        ),
    )
    builds = builds_ready_for(tmp_path, source)
    open_before = {held.name for held in Path("/proc/self/fd").iterdir()}

    built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    assert built.text is not None
    assert f"more than {MAXIMUM_ENTRIES} files" in built.text
    assert list(builds.builds.iterdir()) == []
    assert {held.name for held in Path("/proc/self/fd").iterdir()} <= open_before


def test_a_talk_that_cannot_be_added_up_in_time_is_refused_without_words(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    machine.setattr(builds_module, "_ADDING_UP_WITHIN", _NO_BUDGET_AT_ALL)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    # What this host could not do is nothing a deck's author could act on.
    assert built == BuildFailure(text=None)
    assert EXAMPLE_SLUG in caplog.text
    assert list(builds.builds.iterdir()) == []


def test_a_talk_holding_something_this_server_cannot_read_is_refused(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_CLOSES_WHAT_IT_BUILT)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    # An entry nobody can read is a talk nobody added up, never a talk of none.
    assert built == BuildFailure(text=None)
    assert EXAMPLE_SLUG in caplog.text
    assert list(builds.builds.iterdir()) == []


def test_a_link_out_of_the_run_is_counted_as_the_name_it_is(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside, held = _something_of_this_machines(tmp_path)
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_LEAVES_A_LINK_OUT.format(outside=outside),
    )
    builds = builds_ready_for(
        tmp_path,
        source,
        output_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT,
    )

    artefacts = what_it_built(builds.build(a_deck(remote), source=source))

    # What the link points at is megabytes of this machine, and none of them
    # were followed, read or counted against the talk.
    assert builds.holds(artefacts)
    assert held.exists()


def test_a_link_a_build_left_is_never_opened_when_its_leavings_go(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    outside, held = _something_of_this_machines(tmp_path)
    outside.chmod(_CLOSED_TO_EVERYONE)
    with_the_toolchain(
        machine,
        tmp_path,
        _A_TOOLCHAIN_THAT_LEAVES_A_LINK_OUT_AND_FAILS.format(outside=outside),
    )
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    # Taking a failed build's leavings away opens what that build closed; a
    # directory of this machine it pointed at is not that build's to be opened.
    assert isinstance(built, BuildFailure)
    assert list(builds.builds.iterdir()) == []
    assert stat.S_IMODE(outside.stat().st_mode) == _CLOSED_TO_EVERYONE
    outside.chmod(stat.S_IRWXU)
    assert held.exists()


def _something_of_this_machines(tmp_path: Path) -> tuple[Path, Path]:
    """A directory of this machine, and the megabytes it holds."""
    outside = tmp_path / "of-this-machine"
    outside.mkdir()
    held = outside / "not-a-decks-business"
    held.write_bytes(b"\0" * _MEGABYTES_A_GREEDY_BUILD_WRITES * 1024 * 1024)
    return outside, held


def test_what_a_build_closed_behind_it_is_opened_and_taken_away_all_the_same(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_CLOSES_WHAT_IT_WROTE)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    assert list(builds.builds.iterdir()) == []


def test_what_could_not_be_taken_away_is_named_in_the_log(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_CLOSES_THE_ROOT)
    builds = builds_ready_for(tmp_path, source)

    with _opened_again_afterwards(builds.builds):
        built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    left = list(builds.builds.iterdir())
    assert len(left) == 1
    assert left[0].name in caplog.text


@contextmanager
def _opened_again_afterwards(root: Path) -> Generator[None]:
    """Whatever the build did to the root, leave it removable for the next test."""
    try:
        yield
    finally:
        root.chmod(0o700)


def test_a_talk_that_is_gone_by_the_time_it_is_added_up_is_refused(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The step keeps running after taking its own output away, so only a
    # watch taken while it is still going catches it gone: a machine is
    # filled, or here emptied, while a build runs, not once it has ended.
    machine.setattr(builds_module, "_ACCOUNTED_EVERY", _WATCHED_OFTEN)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_TAKES_ITS_OWN_TALK_AWAY)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    # A build that took its own talk away left nothing to add up and nothing
    # to deliver; what was written is not guessed at.
    assert built == BuildFailure(text=None)
    assert EXAMPLE_SLUG in caplog.text


def test_a_run_that_took_itself_away_leaves_the_next_build_nothing_to_do(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_TAKES_THE_WHOLE_RUN_AWAY)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, BuildFailure)
    assert list(builds.builds.iterdir()) == []


def test_a_name_that_is_no_longer_a_directory_when_it_is_opened_is_refused(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A build turning one of its own directories into a link between the
    # moment it is read and the moment it is opened cannot be arranged on
    # demand; what that leaves is an open that fails, and this is that.
    def a_link_by_now(name: str, *, held_by: int) -> int:
        del name, held_by
        raise OSError(ELOOP, "the name is a link now")

    machine.setattr(builds_module, "_opened_within", a_link_by_now)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    built = builds.build(a_deck(remote), source=source)

    # Refused, and never followed: nothing of this machine was added up.
    assert built == BuildFailure(text=None)
    assert EXAMPLE_SLUG in caplog.text
    assert list(builds.builds.iterdir()) == []
