"""Turning a deck's folder into the talk it delivers, with Slidev (ADR 0005).

Slidev's own command line owns the build and the PDF export, so this adapter
spawns it rather than binding a second renderer. The deck's tree is written out
of the bare mirror beside it, because a toolchain needs files.

**A deck is code.** Where that code runs is this module's second question. The
deployed answer is the build worker, a sealed process of its own that this module
reaches through `DeckToolchain`. Running the toolchain on this host instead is the
development answer, and there a deck's Vue components run with this process's
rights.

What a build may take is bounded on every side it has: the time it may run, what
it may write into its talk, and how much of what it prints this server ever
holds.
"""

import json
import logging
import os
import select
import signal
import stat
import subprocess
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from tempfile import mkdtemp
from time import monotonic
from typing import IO, BinaryIO, Final, Protocol, cast

from gitmirror.model import MirrorError, Revision
from presentator.adapters.build_runs import (
    RUN_DIRECTORY_MODE,
    listed_runs,
    removed_run,
    run_name_of,
    taken_away,
)
from presentator.adapters.build_wire import MAXIMUM_ENTRIES
from presentator.adapters.decks import SourceMirrors
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    FAILURE_TAIL_BYTES,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    SLIDES_FILE,
    TALK_DIRECTORY,
    TALK_PAGE,
    Artefacts,
    BuildFailure,
    BuildFailureReason,
    Deck,
    Source,
    StepKind,
    failure_text,
    talk_address,
)
from presentator.ports.decks import BuildRun

_TOOLCHAIN: Final = "pnpm"
_SLIDEV: Final = "slidev"
_PACKAGE_JSON: Final = "package.json"
# A deck names only what the toolchain project carries (ADR 0014): a package
# under this prefix is a Slidev theme, named on a deck page by what follows
# the prefix. `default` ships with Slidev itself, so it counts whether or not
# the project's own manifest lists it — package.json's own shape, not this
# product's, is what a reader of it has to trust or refuse.
_THEME_PACKAGE_PREFIX: Final = "@slidev/theme-"
_DEFAULT_THEME: Final = "default"
_DEPENDENCY_SECTIONS: Final = ("dependencies", "devDependencies")
_UP: Final = ".."
# Only what a Node toolchain needs to run; a step inherits nothing else, so
# neither a source's read-only secret nor this instance's key is in reach of a
# deck's own build-time code.
_INHERITED: Final = ("PATH", "HOME")
_READ_AT_ONCE: Final = 64 * 1024
_A_MEGABYTE: Final = 1024 * 1024
# Adding up what a build wrote is walking a tree that build filled, so it is
# bounded like everything else it touches: a talk is hundreds of files, and
# adding up even a hundred thousand of them is a moment's work. Past either
# bound the build is refused rather than added up in the dark.
_ADDING_UP_WITHIN: Final = timedelta(seconds=10)
# How often a step is asked what it has written by now, so a build that fills
# this machine is stopped while it runs rather than found out afterwards.
_ACCOUNTED_EVERY: Final = timedelta(seconds=2)
_UNREADABLE_DECK: Final = "deck %s cannot be read at %s: %s"
_TOOLCHAIN_FAILED: Final = "%s of deck %s failed: %s"
_TOOLCHAIN_UNAVAILABLE: Final = "%s of deck %s could not be run: %s"
_TOOLCHAIN_TIMED_OUT: Final = "%s of deck %s ran past %s and was given up"
_NOT_AS_WRITTEN: Final = (
    "the talk at %s is refused: %s is not a file reached without a link under the root"
)
_TOO_MUCH_WRITTEN: Final = "deck %s wrote %s MB, which is more than a build may leave"
_UNACCOUNTABLE: Final = "what the build of deck %s wrote could not be added up: %s"
_TOOK_TOO_LONG_TO_ADD_UP: Final = (
    "what the build of deck %s wrote could not be added up inside %s"
)

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class _Added:
    """What adding up one run's talk found, and what stopped the adding up.

    A pass that was stopped carries no size: it says how far it came, and the
    caller refuses the build rather than believing a number nobody counted.
    """

    entries: int
    megabytes: int = 0
    unreadable: str | None = None
    in_time: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildPlace:
    """Where one run of the build works: the root, and its own directory in it.

    That directory is named relative to the root, because the root is the one
    place two filesystems both know: this server's, and that of the worker the
    build runs in.
    """

    root: Path
    run: Path

    @property
    def here(self) -> Path:
        """That run's own directory, as this server names it."""
        return self.root / self.run


@dataclass(frozen=True, slots=True, kw_only=True)
class _HowItIsRun:
    """How one runner starts a step and bounds it."""

    in_directory: Path
    environment: dict[str, str]
    bound: timedelta


@dataclass(frozen=True, slots=True, kw_only=True)
class _HowItWent:
    """What a step printed, and what stopped it before it was done."""

    said: bytes
    refused: BuildFailure | None
    in_time: bool


def slidev_arguments(
    kind: StepKind, *, place: BuildPlace, slug: str
) -> tuple[str, ...]:
    """The arguments Slidev is given for one step of a deck's build."""
    entry = str(place.here / DECK_DIRECTORY / SLIDES_FILE)
    written = place.here / OUTPUT_DIRECTORY
    flags = {
        StepKind.BUILD: (
            "--base",
            talk_address(slug),
            "--out",
            str(written / TALK_DIRECTORY),
        ),
        StepKind.EXPORT: ("--output", str(written / PDF_FILE)),
    }
    return (entry, *flags[kind])


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildStep:
    """One Slidev command of one build, and the deck it is run for.

    The paths in the arguments are this server's own. A toolchain that runs the
    command somewhere else gives that place the very same paths, so a step's
    command line says the same thing wherever it runs.
    """

    place: BuildPlace
    slug: str
    name: StepKind
    arguments: tuple[str, ...]
    # Asked while the step runs: a build that has already written more than it
    # may leave is stopped there, because by the time it ends this machine is
    # full. It answers with what the deck's page will say, or with nothing.
    watched: Callable[[], BuildFailure | None]


class DeckToolchain(Protocol):
    """Runs one Slidev command over the directories a build works in."""

    @abstractmethod
    def ran(self, step: BuildStep) -> BuildFailure | None:
        """Run that step, saying nothing when it did what it was asked to.

        What the toolchain printed travels back with a failure, because the
        deck's page shows it (line 9); a step that was given up on, and one
        that could not be started at all, hand back no words.
        """


@dataclass(frozen=True, slots=True, kw_only=True)
class HostToolchain:
    """Runs Slidev out of a Node project on this machine, as this process.

    A deck's own components then run with this server's rights over the
    filesystem and the network; only the environment, the time, and where the
    result may stand are bounded. It is what a development run builds with, and
    only a deployment that says so in as many words gets it.
    """

    project: Path
    bound: timedelta

    def ran(self, step: BuildStep) -> BuildFailure | None:
        """Run that step out of this machine's own toolchain project."""
        return _what_a_step_said(
            (_TOOLCHAIN, "exec", _SLIDEV, step.name, *step.arguments),
            step=step,
            how=_HowItIsRun(
                in_directory=self.project,
                environment=_the_environment_of_this_server(),
                bound=self.bound,
            ),
        )


def theme_names_of_manifest(manifest_text: str) -> tuple[str, ...]:
    """The themes that manifest's dependencies carry, `default` always among them.

    Not underscore-prefixed: `PackageJsonThemes.names` below is the only
    caller across the product, but this parsing step is proven directly
    against `package.json`'s own shape rather than only through that wrapper.

    Every `@slidev/theme-*` package `dependencies` or `devDependencies` name
    counts, stripped of that prefix; widening the set is one change to
    `package.json` (ADR 0014), and this is its only reader. JSON this is not,
    an object this is not, or a dependency section that is not a mapping of
    package name to version string, is not a set to derive a guess from, so
    each of them raises rather than answering with one.
    """
    parsed: object = json.loads(manifest_text)
    if not isinstance(parsed, dict):
        message = "package.json is not a JSON object"
        raise TypeError(message)
    manifest = cast("dict[str, object]", parsed)
    named = {
        package.removeprefix(_THEME_PACKAGE_PREFIX)
        for section in _DEPENDENCY_SECTIONS
        for package in _dependency_names(manifest, section)
        if package.startswith(_THEME_PACKAGE_PREFIX)
    }
    named.add(_DEFAULT_THEME)
    return tuple(sorted(named))


def _dependency_names(manifest: dict[str, object], section: str) -> tuple[str, ...]:
    """That section's package names, refusing a shape that is not one.

    A missing section is no dependency at all, so it counts as empty; a
    section that is not a mapping of package name to version string is not a
    dependency list a theme name can be read from, so it is refused rather
    than guessed at.
    """
    entries = manifest.get(section)
    if entries is None:
        return ()
    if not isinstance(entries, dict):
        message = f"{section} is not an object"
        raise TypeError(message)
    pairs = cast("dict[object, object]", entries)
    names: list[str] = []
    for name, version in pairs.items():
        if not isinstance(name, str) or not isinstance(version, str):
            message = f"{section} is not a mapping of package name to version"
            raise TypeError(message)
        names.append(name)
    return tuple(names)


@dataclass(frozen=True, slots=True, kw_only=True)
class PackageJsonThemes:
    """The theme names read off the toolchain project's `package.json` (ADR 0014).

    Read at every call rather than cached at start: the file is small, on this
    machine's own disk, and read is far cheaper than the toolchain step it
    stands beside. Caching it would need its own invalidation the moment an
    operator swaps `PRESENTATOR_TOOLCHAIN`'s contents without a restart, for a
    cost this read does not have.
    """

    project: Path

    def names(self) -> tuple[str, ...] | None:
        """This project's theme names, or nothing while its manifest cannot be read."""
        try:
            manifest_text = (self.project / _PACKAGE_JSON).read_text()
            return theme_names_of_manifest(manifest_text)
        except OSError, ValueError, TypeError:
            # A missing project, an unreadable file, malformed JSON, and a
            # manifest or dependency section whose shape package.json's own
            # convention does not carry are all "cannot read" here: none of
            # them is a set to derive a guess from (R3), so a deck page reads
            # any of them the same way, as no row.
            return None


@dataclass(frozen=True, slots=True, kw_only=True)
class SlidevBuilds:
    """Builds decks with the Slidev toolchain into directories under one root."""

    builds: Path
    toolchain: DeckToolchain
    mirrors: SourceMirrors
    output_megabytes: int

    def build(self, deck: Deck, *, source: Source) -> Artefacts | BuildFailure:
        """Build this deck at its commit, or say what came of it instead.

        The talk and the PDF are written under a directory of this build's own
        that nothing points at yet, and the directory the deck delivered from
        before is left where it stands. What the toolchain said about a failure
        travels back with it, because the deck's page shows it (line 9). A run
        that ends any other way than handing back a talk — a failure this
        method returns, or a fault one of its own steps could not turn into
        one — still leaves its directory cleaned up, because a run neither
        delivered nor removed is one this instance can never try again.
        """
        place = self._a_place_of_its_own()
        delivered = False
        try:
            broke = self._what_broke(deck, source=source, place=place)
            if broke is not None:
                return broke
            written = place.here / OUTPUT_DIRECTORY
            delivered = True
            return Artefacts(directory=written / TALK_DIRECTORY, pdf=written / PDF_FILE)
        finally:
            if not delivered:
                taken_away(place.here)

    def holds(self, artefacts: Artefacts) -> bool:
        """Whether the talk's page and the PDF really stand under the root as files.

        A deck's own code wrote every name below the root, so none of them is
        followed: each directory on the way is opened from the one holding it,
        never through a link, and what stands at the end has to be a regular
        file. A link anywhere on either way refuses the build — even one that
        points back inside the root — because an address this server hands out
        must never come to mean a file the build did not write there.
        """
        root = self.builds.absolute()
        for written in (artefacts.directory / TALK_PAGE, artefacts.pdf):
            found = opened_without_a_link(written, under=root, how=os.O_PATH)
            if found is None:
                _log.warning(_NOT_AS_WRITTEN, artefacts.directory, written)
                return False
            os.close(found)
        return True

    def read(self, written: Path) -> BinaryIO | None:
        """That file of a build for reading, or nothing where it is not one.

        The check and the open are one step: what is handed back was opened
        from the root down without following a link, so a name a build swapped
        for a link since `holds` looked cannot lead anywhere this server did
        not write.
        """
        found = opened_without_a_link(written, under=self.builds.absolute())
        return None if found is None else os.fdopen(found, "rb")

    def runs(self) -> frozenset[BuildRun] | None:
        """Every run directly under the root, or nothing while it cannot be listed."""
        names = listed_runs(self.builds.absolute())
        return None if names is None else frozenset(map(BuildRun, names))

    def run_of(self, path: Path) -> BuildRun | None:
        """The run a stored talk or PDF path belongs to, by its exact shape."""
        name = run_name_of(self.builds.absolute(), path)
        return None if name is None else BuildRun(name)

    def remove(self, run: BuildRun) -> bool:
        """Take that run's whole directory off disk, or say it is still there."""
        return removed_run(self.builds.absolute(), run.name)

    def _a_place_of_its_own(self) -> BuildPlace:
        """Where this run works: a directory of its own under the root.

        Its name is this server's own and carries nothing of the deck, because
        it becomes a worker's directory name — and a folder name
        somebody else chose would be somebody else's punctuation in a command
        line of ours.
        """
        root = self.builds.absolute()
        root.mkdir(parents=True, exist_ok=True)
        run = Path(mkdtemp(dir=root))
        run.chmod(RUN_DIRECTORY_MODE)
        (run / OUTPUT_DIRECTORY).mkdir()
        return BuildPlace(root=root, run=Path(run.name))

    def _what_broke(
        self,
        deck: Deck,
        *,
        source: Source,
        place: BuildPlace,
    ) -> BuildFailure | None:
        """Write the deck out, build the talk, export the PDF, or say what broke."""
        if not self._exported(deck, source=source, into=place.here / DECK_DIRECTORY):
            # A tree this server cannot read out of its own mirror is a fault
            # of this host, not words a deck's author could act on.
            return BuildFailure.without_words()
        # The walk stops at the first step that failed, so a deck that does not
        # build is never exported either.
        for kind in StepKind:
            broke = self.toolchain.ran(
                BuildStep(
                    place=place,
                    slug=deck.slug,
                    name=kind,
                    arguments=slidev_arguments(kind, place=place, slug=deck.slug),
                    watched=lambda: self._more_than_it_may_leave(deck, place),
                ),
            )
            if broke is not None:
                return broke
        return self._more_than_it_may_leave(deck, place)

    def _more_than_it_may_leave(
        self,
        deck: Deck,
        place: BuildPlace,
    ) -> BuildFailure | None:
        """Refuse a build whose talk is more than one build may leave here.

        A deck's own code decides what its talk holds, and a talk this machine
        cannot keep is a machine that stops keeping the others. It is asked
        while the build runs and once more when it is over, because a build
        that fills this machine has filled it long before it ends. No toolchain
        failed, so the reason a page words here is this server's own; what only
        this host can act on stays in the log.
        """
        added = _added_up(
            place.here / OUTPUT_DIRECTORY,
            ends_by=monotonic() + _ADDING_UP_WITHIN.total_seconds(),
        )
        if added.unreadable is not None:
            _log.warning(_UNACCOUNTABLE, deck.slug, added.unreadable)
            return BuildFailure.without_words()
        if not added.in_time:
            _log.warning(_TOOK_TOO_LONG_TO_ADD_UP, deck.slug, _ADDING_UP_WITHIN)
            return BuildFailure.without_words()
        if added.entries > MAXIMUM_ENTRIES:
            _log.warning(_TOO_MUCH_WRITTEN, deck.slug, added.entries)
            return BuildFailure(
                reason=BuildFailureReason.OUTPUT_ENTRIES,
                allowed_entries=MAXIMUM_ENTRIES,
            )
        if added.megabytes <= self.output_megabytes:
            return None
        _log.warning(_TOO_MUCH_WRITTEN, deck.slug, added.megabytes)
        return BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=added.megabytes,
            allowed_megabytes=self.output_megabytes,
        )

    def _exported(self, deck: Deck, *, source: Source, into: Path) -> bool:
        """Write the deck's tree at its commit out of the mirror, as files."""
        mirror = self.mirrors.of(source)
        if mirror is None:
            _log.warning(
                _UNREADABLE_DECK,
                deck.slug,
                deck.commit,
                "the source no longer resolves to a readable repository",
            )
            return False
        try:
            mirror.export(
                Revision(ref=source.ref, commit=deck.commit),
                deck.slug,
                into=into,
            )
        except MirrorError as unreadable:
            _log.warning(_UNREADABLE_DECK, deck.slug, deck.commit, unreadable)
            return False
        return True


def _what_a_step_said(
    command: tuple[str, ...],
    *,
    step: BuildStep,
    how: _HowItIsRun,
) -> BuildFailure | None:
    """Run one step's command line under the bound, and say what came of it."""
    reading, writing = os.pipe()
    with os.fdopen(reading, "rb", buffering=0) as printed:
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is the build step command line with no shell and run in its own session under a bound
                command,
                stdout=subprocess.DEVNULL,
                stderr=writing,
                cwd=how.in_directory,
                env=how.environment,
                # A step spawns processes of its own — pnpm runs slidev, and an
                # export runs a browser under that — so only a session of its
                # own lets the bound reach every one of them, not just the
                # child this call started directly.
                start_new_session=True,
            )
        except OSError as unavailable:
            # A toolchain that cannot be started is this host's fault, and what
            # the operating system says about it names paths of this host; the
            # deck's page is told that no words came back, and the log keeps
            # them.
            _log.exception(_TOOLCHAIN_UNAVAILABLE, step.name, step.slug, unavailable)
            return BuildFailure.without_words()
        finally:
            # The child holds the writing end now, and only this process
            # letting go of it lets the reading end ever see an end.
            os.close(writing)
        went = _the_end_of_what_it_printed(printed, process, step=step, bound=how.bound)
    if went.refused is not None or not went.in_time:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        process.wait()
        if went.refused is not None:
            # What it wrote is the reason, and the watcher has said it.
            return went.refused
        _log.warning(_TOOLCHAIN_TIMED_OUT, step.name, step.slug, how.bound)
        # What a step that was killed had printed is not shown: it is the words
        # of a run nobody waited for, and the page says so in its own sentence.
        return BuildFailure.without_words()
    if process.returncode != 0:
        words = failure_text(went.said)
        _log.warning(_TOOLCHAIN_FAILED, step.name, step.slug, words)
        return BuildFailure.said(words)
    return None


def _the_end_of_what_it_printed(
    printed: IO[bytes],
    process: subprocess.Popen[bytes],
    *,
    step: BuildStep,
    bound: timedelta,
) -> _HowItWent:
    """The last of what a step printed, and what stopped it before it was done.

    A deck's own build decides how much it prints, and a server that held all
    of it would be a server a deck can fill; only the end a page can show is
    ever kept, however long the step goes on talking. The same wait is what
    asks, at every turn, whether the build has by now written more than it may
    leave — a machine is filled while a build runs, not when it ends.
    """
    ends_by = monotonic() + bound.total_seconds()
    accounted_at = monotonic()
    said = b""
    while True:
        now = monotonic()
        if now >= ends_by:
            return _HowItWent(said=said, refused=None, in_time=False)
        if now >= accounted_at:
            refused = step.watched()
            if refused is not None:
                return _HowItWent(said=said, refused=refused, in_time=True)
            # On its own turn, whatever the step is printing: a build that
            # talks without stopping would otherwise be counted at every word.
            accounted_at = monotonic() + _ACCOUNTED_EVERY.total_seconds()
        waited_for = max(min(ends_by, accounted_at) - monotonic(), 0.0)
        if not select.select([printed], [], [], waited_for)[0]:
            continue
        printed_now = printed.read(_READ_AT_ONCE)
        if not printed_now:
            return _HowItWent(
                said=said,
                refused=None,
                in_time=_ended_within(process, ends_by),
            )
        said = (said + printed_now)[-FAILURE_TAIL_BYTES:]


def _ended_within(process: subprocess.Popen[bytes], ends_by: float) -> bool:
    """Whether the step's own process was over by then, once it stopped talking."""
    try:
        process.wait(timeout=max(ends_by - monotonic(), 0.0))
    except subprocess.TimeoutExpired:
        return False
    return True


def _added_up(directory: Path, *, ends_by: float) -> _Added:
    """Add up what one run has written into that directory, so far.

    Every name in it was written by a deck's own build, so nothing here is
    reached by its path: the root is opened once, and every directory inside
    it is opened from the descriptor of the one that holds it, as a directory
    and never through a link. A name that is not a real directory by the time
    it is opened is refused rather than followed — between one look and the
    next a build can make a name into anything, and a descriptor is the one
    thing it cannot exchange. A link is counted as the name it is, the walk
    stops at the first entry it cannot read, and it stops at the entry and at
    the moment it may not go past: a talk this server could not add up is one
    it refuses, never one it guesses at.
    """
    root = _a_directory_of_its_own(directory)
    if root is None:
        return _Added(entries=0, unreadable=str(directory))
    still_open = [root]
    try:
        return _walked(still_open, ends_by=ends_by)
    finally:
        for holder in still_open:
            os.close(holder)


def _walked(still_open: list[int], *, ends_by: float) -> _Added:
    held, entries = 0, 0
    while still_open:
        holder = still_open.pop()
        try:
            with os.scandir(holder) as listing:
                for entry in listing:
                    entries += 1
                    stopped = _stopped_at(entries, ends_by=ends_by)
                    if stopped is not None:
                        return stopped
                    held += _size_of(entry, held_by=holder, still_open=still_open)
        except OSError as unreadable:
            return _Added(entries=entries, unreadable=str(unreadable))
        finally:
            os.close(holder)
    return _Added(entries=entries, megabytes=held // _A_MEGABYTE)


def _stopped_at(entries: int, *, ends_by: float) -> _Added | None:
    if entries > MAXIMUM_ENTRIES:
        return _Added(entries=entries)
    if monotonic() > ends_by:
        return _Added(entries=entries, in_time=False)
    return None


def _size_of(entry: os.DirEntry[str], *, held_by: int, still_open: list[int]) -> int:
    if entry.is_dir(follow_symlinks=False):
        still_open.append(_opened_within(entry.name, held_by=held_by))
        return 0
    return 0 if entry.is_symlink() else entry.stat(follow_symlinks=False).st_size


def _opened_within(name: str, *, held_by: int) -> int:
    """That name as a directory of its own, opened from the one holding it.

    The open is the whole check: it refuses a link and it refuses anything
    that is not a directory, at the moment it happens, so nothing a build does
    in between can turn the name it read into a way out of this run.
    """
    return os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
        dir_fd=held_by,
    )


def opened_without_a_link(
    written: Path,
    *,
    under: Path,
    how: int = os.O_RDONLY,
) -> int | None:
    """That path as a descriptor, if it is a regular file and no name on it a link.

    The root is this deployment's own and opened as it is configured; from
    there on each directory is opened from the descriptor of the one holding
    it, and the last name is looked at itself rather than at what it may point
    to. A name that is not a regular file is closed again, and never read: it
    is opened without waiting, so a pipe a build left cannot hold a request.
    """
    if not written.is_relative_to(under):
        return None
    names = written.relative_to(under).parts
    if not names or _UP in names:
        return None
    *directories, name = names
    try:
        holder = os.open(under, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return None
    try:
        for directory in directories:
            inner = _opened_within(directory, held_by=holder)
            os.close(holder)
            holder = inner
        found = os.open(name, how | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=holder)
    except OSError, ValueError:
        return None
    finally:
        os.close(holder)
    if stat.S_ISREG(os.fstat(found).st_mode):
        return found
    os.close(found)
    return None


def _a_directory_of_its_own(directory: Path) -> int | None:
    """That directory as a descriptor, or nothing where it is not one."""
    try:
        return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return None


def _the_environment_of_this_server() -> dict[str, str]:
    """The whole environment a step's own command line is run with."""
    return {name: os.environ[name] for name in _INHERITED if name in os.environ}
