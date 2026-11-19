"""Building a deck with a toolchain on PATH, against a real bare repository.

The toolchain here is a script this test wrote, not Slidev: what belongs to
this adapter is which command it runs, in which environment, under which bound,
and where the result is allowed to stand. That Slidev itself builds a deck, and
that the worker really denies it this machine, is proven by driving the real
interface.
"""

import os
import stat
import subprocess
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from errno import ELOOP
from pathlib import Path, PurePosixPath

import pytest

from presentator.adapters import build_tree
from presentator.adapters import builds as builds_module
from presentator.adapters.build_wire import MAXIMUM_ENTRIES
from presentator.contracts.decks import (
    FAILURE_TEXT_LIMIT,
    Artefacts,
    BuildFailure,
    BuildFailureReason,
    BuildRun,
    Deck,
    RunLeft,
    Source,
    talk_address,
)
from tests.presentator.adapters.support.build_support import (
    A_GENEROUS_BOUND as _A_GENEROUS_BOUND,
)
from tests.presentator.adapters.support.build_support import (
    A_TOOLCHAIN_THAT_BUILDS as _A_TOOLCHAIN_THAT_BUILDS,
)
from tests.presentator.adapters.support.build_support import (
    FINDS_ITS_OUTPUT as _FINDS_ITS_OUTPUT,
)
from tests.presentator.adapters.support.build_support import (
    TOOLCHAIN_PROGRAM as _TOOLCHAIN_PROGRAM,
)
from tests.presentator.adapters.support.build_support import (
    builds_ready_for,
    builds_under,
    lines_of,
    on_this_machine,
    the_error_line,
    what_it_built,
    with_the_toolchain,
)
from tests.support.git_remote import (
    EXAMPLE_SLUG,
    MAIN_BRANCH,
    GitRemote,
    a_path_carrying,
    with_the_program,
)
from tests.support.log_lines import json_logged

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_ONLY_A_MEGABYTE_MAY_BE_LEFT = 1
_MEGABYTES_A_GREEDY_BUILD_WRITES = 3
_WATCHED_OFTEN = timedelta(milliseconds=50)
_CLOSED_TO_EVERYONE = 0o000
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
_WHAT_A_TALK_SAYS = "a talk"

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
_A_TOOLCHAIN_THAT_FAILS_SILENTLY = """#!/bin/sh
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
# wrote it; the pdf is moved in whole, so no watch counts it half-written.
_A_TOOLCHAIN_THAT_WRITES = (
    _FINDS_ITS_OUTPUT
    + """a_megabyte=1048576; p="${{out%/*}}/../partial"
case "$out" in
    *.pdf)
        dd if=/dev/zero of="$p" bs=$a_megabyte count={pdf} 2>/dev/null && mv "$p" "$out"
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


@pytest.fixture
def machine(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A machine whose environment holds a secret no build may ever see."""
    monkeypatch.setenv(_A_SERVER_SECRET, _WHAT_THE_SERVER_HOLDS)
    return monkeypatch


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


def test_a_build_of_a_source_whose_address_names_no_access_is_not_attempted(
    remote: GitRemote,
    source: Source,
    tmp_path: Path,
) -> None:
    """No git process is asked to open an address no access names."""
    builds = builds_under(tmp_path, toolchain=on_this_machine(tmp_path))
    source = replace(source, url="ftp://git.example.invalid/talks.git")

    built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure.without_words()


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
        (
            _A_TOOLCHAIN_THAT_FAILS,
            _A_GENEROUS_BOUND,
            _WHAT_A_FAILING_TOOLCHAIN_SAID,
            "toolchain_failed",
        ),
        (
            _A_TOOLCHAIN_THAT_FAILS_SILENTLY,
            _A_GENEROUS_BOUND,
            None,
            "toolchain_failed",
        ),
        (_A_TOOLCHAIN_THAT_NEVER_ENDS, _NO_BUDGET_AT_ALL, None, "timed_out"),
    ],
    ids=[
        "a deck that does not build",
        "a deck that does not build and says nothing",
        "a build that runs past its bound",
    ],
)
def test_a_build_that_did_not_finish_says_why_and_leaves_nothing_to_point_at(
    remote: GitRemote,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    toolchain: tuple[str, timedelta, str | None, str],
) -> None:
    script, bound, said, reason = toolchain
    source = a_source(remote)
    with_the_toolchain(machine, tmp_path, script)

    builds = builds_ready_for(
        tmp_path, source, toolchain=on_this_machine(tmp_path, bound=bound)
    )
    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure.said(said)
    assert list(builds.builds.iterdir()) == []
    (step,) = lines_of(logged, "build_step.ended")
    assert (step["step"], step["outcome"], step["reason"]) == (
        "build",
        "failed",
        reason,
    )
    assert step["level"] == "warning"
    (ended,) = lines_of(logged, "deck_build.ended")
    assert ended["outcome"] == "failed"
    assert not [one for one in logged.lines if one["level"] == "error"]
    assert EXAMPLE_SLUG not in logged.text
    assert _WHAT_A_FAILING_TOOLCHAIN_SAID not in logged.text


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

    assert built == BuildFailure.without_words()
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

    assert built == BuildFailure.without_words()
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    builds = builds_ready_for(tmp_path, source)
    deck = a_deck(remote)
    machine.setenv("PATH", a_path_carrying())

    with json_logged(capsys) as logged:
        built = builds.build(deck, source=source)

    # What the operating system says names paths of this host, so no line
    # carries it; the page is told that this build left no words.
    assert built == BuildFailure.without_words()
    step = the_error_line(logged)
    assert step["event"] == "build_step.ended"
    (build,) = lines_of(logged, "deck_build.ended")
    assert (build["outcome"], build["level"]) == ("failed", "warning")
    assert step["reason"] == build["reason"] == "toolchain_unavailable"
    assert EXAMPLE_SLUG not in logged.text
    assert _TOOLCHAIN_PROGRAM not in logged.text


def test_a_commit_the_mirror_cannot_read_the_deck_at_builds_nothing(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)

    with json_logged(capsys) as logged:
        built = builds_ready_for(tmp_path, source).build(
            a_deck(remote, slug="never-pushed"),
            source=source,
        )

    # A tree this host could not read out of its own mirror is nothing a deck's
    # author could act on, so the page is told nothing and the line holds why.
    assert built == BuildFailure.without_words()
    line = the_error_line(logged)
    assert (line["event"], line["reason"]) == ("deck_build.ended", "unreadable_deck")
    assert "never-pushed" not in logged.text


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
    elsewhere = tmp_path / "kept-by-the-server"
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


def test_a_talk_larger_than_a_build_may_leave_is_refused_with_that_reason(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No watch may see a half-written file: the final check adds up what was left.
    machine.setattr(builds_module, "_ACCOUNTED_EVERY", timedelta(hours=1))
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

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    assert built == BuildFailure(
        reason=BuildFailureReason.OUTPUT_MEGABYTES,
        written_megabytes=_MEGABYTES_A_GREEDY_BUILD_WRITES,
        allowed_megabytes=_ONLY_A_MEGABYTE_MAY_BE_LEFT,
    )
    assert list(builds.builds.iterdir()) == []
    (ended,) = lines_of(logged, "deck_build.ended")
    assert (ended["outcome"], ended["reason"], ended["level"]) == (
        "refused",
        BuildFailureReason.OUTPUT_MEGABYTES,
        "info",
    )
    assert ended["written_megabytes"] == _MEGABYTES_A_GREEDY_BUILD_WRITES


def test_a_build_that_delivers_writes_one_line_per_step_and_one_for_the_build(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    assert isinstance(built, Artefacts)
    steps = lines_of(logged, "build_step.ended")
    assert [(one["step"], one["outcome"]) for one in steps] == [
        ("build", "ok"),
        ("export", "ok"),
    ]
    (ended,) = lines_of(logged, "deck_build.ended")
    assert ended["outcome"] == "ok"
    assert ended["entry_count"] == len(list(built.directory.parent.rglob("*")))
    assert all(one["deck_id"] == ended["deck_id"] for one in steps)
    assert isinstance(ended["duration_ms"], float)
    assert EXAMPLE_SLUG not in logged.text
    assert str(tmp_path) not in logged.text


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
    assert getattr(built, "reason", None) is BuildFailureReason.OUTPUT_MEGABYTES
    assert list(builds.builds.iterdir()) == []


def test_too_many_files_are_refused_and_leave_no_listing_open(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
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
    assert built == BuildFailure(
        reason=BuildFailureReason.OUTPUT_ENTRIES, allowed_entries=MAXIMUM_ENTRIES
    )
    assert list(builds.builds.iterdir()) == []
    assert {held.name for held in Path("/proc/self/fd").iterdir()} <= open_before
    assert not [w for w in recwarn if w.category is ResourceWarning]


def test_a_talk_that_cannot_be_added_up_in_time_is_refused_without_words(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    machine.setattr(builds_module, "_ADDING_UP_WITHIN", _NO_BUDGET_AT_ALL)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    # What this host could not do is nothing a deck's author could act on.
    assert built == BuildFailure.without_words()
    assert the_error_line(logged)["reason"] == "adding_up_timed_out"
    assert EXAMPLE_SLUG not in logged.text
    assert list(builds.builds.iterdir()) == []


def test_a_talk_holding_something_this_server_cannot_read_is_refused(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_CLOSES_WHAT_IT_BUILT)
    builds = builds_ready_for(tmp_path, source)

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    # An entry nobody can read is a talk nobody added up, never a talk of none.
    assert built == BuildFailure.without_words()
    assert the_error_line(logged)["reason"] == "unaccountable"
    assert EXAMPLE_SLUG not in logged.text
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


def test_a_run_that_could_not_be_taken_away_is_logged_and_answered_as_its_volume_path(
    remote: GitRemote,
    source: Source,
    machine: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_CLOSES_THE_ROOT)
    builds = builds_ready_for(tmp_path, source)

    with _opened_again_afterwards(builds.builds), json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)
        (left,) = builds.builds.iterdir()
        stayed = lines_of(logged, "build_run_removal.refused")
        named = builds.remove(BuildRun(left.name))

    assert isinstance(built, BuildFailure)
    assert [one["run_id"] for one in stayed] == [left.name]
    assert named == RunLeft(
        directory=PurePosixPath(builds.builds.name, left.name), in_store=False
    )


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
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The step keeps running after taking its own output away, so only a
    # watch taken while it is still going catches it gone: a machine is
    # filled, or here emptied, while a build runs, not once it has ended.
    machine.setattr(builds_module, "_ACCOUNTED_EVERY", _WATCHED_OFTEN)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_TAKES_ITS_OWN_TALK_AWAY)
    builds = builds_ready_for(tmp_path, source)

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    # A build that took its own talk away left nothing to add up and nothing
    # to deliver; what was written is not guessed at.
    assert built == BuildFailure.without_words()
    assert the_error_line(logged)["reason"] == "unaccountable"
    assert EXAMPLE_SLUG not in logged.text


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
    capsys: pytest.CaptureFixture[str],
) -> None:
    # A build turning one of its own directories into a link between the
    # moment it is read and the moment it is opened cannot be arranged on
    # demand; what that leaves is an open that fails, and this is that.
    def a_link_by_now(name: str, *, held_by: int) -> int:
        del name, held_by
        raise OSError(ELOOP, "the name is a link now")

    machine.setattr(build_tree, "opened_within", a_link_by_now)
    with_the_toolchain(machine, tmp_path, _A_TOOLCHAIN_THAT_BUILDS)
    builds = builds_ready_for(tmp_path, source)

    with json_logged(capsys) as logged:
        built = builds.build(a_deck(remote), source=source)

    # Refused, and never followed: nothing of this machine was added up.
    assert built == BuildFailure.without_words()
    assert the_error_line(logged)["reason"] == "unaccountable"
    assert EXAMPLE_SLUG not in logged.text
    assert list(builds.builds.iterdir()) == []
