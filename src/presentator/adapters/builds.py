"""Turning a deck's folder into the talk it delivers, with Slidev.

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
import subprocess
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from time import monotonic
from typing import IO, BinaryIO, Final, Protocol, cast

from logline import StepTimer

from gitmirror.model import MirrorError, Revision
from presentator.adapters.build_cache import BuildCache
from presentator.adapters.build_runs import RUN_DIRECTORY_MODE, taken_away
from presentator.adapters.build_tree import added_up, opened_without_a_link
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
    Build,
    BuildFailure,
    BuildFailureReason,
    BuildRun,
    Deck,
    Source,
    StepKind,
    ToolchainIdentity,
    ToolchainUnavailable,
    failure_text,
    talk_address,
)

_TOOLCHAIN: Final = "pnpm"
_SLIDEV: Final = "slidev"
_PACKAGE_JSON: Final = "package.json"
# A deck names only what the toolchain project carries: a package
# under this prefix is a Slidev theme, named on a deck page by what follows
# the prefix. `default` ships with Slidev itself, so it counts whether or not
# the project's own manifest lists it — package.json's own shape, not this
# product's, is what a reader of it has to trust or refuse.
_THEME_PACKAGE_PREFIX: Final = "@slidev/theme-"
_DEFAULT_THEME: Final = "default"
_DEPENDENCY_SECTIONS: Final = ("dependencies", "devDependencies")
# Only what a Node toolchain needs to run; a step inherits nothing else, so
# neither a source's read-only secret nor this instance's key is in reach of a
# deck's own build-time code.
_INHERITED: Final = ("PATH", "HOME")
_READ_AT_ONCE: Final = 64 * 1024
# Adding up what a build wrote is walking a tree that build filled, so it is
# bounded like everything else it touches: a talk is hundreds of files, and
# adding up even a hundred thousand of them is a moment's work. Past either
# bound the build is refused rather than added up in the dark.
_ADDING_UP_WITHIN: Final = timedelta(seconds=10)
# How often a step is asked what it has written by now, so a build that fills
# this machine is stopped while it runs rather than found out afterwards.
_ACCOUNTED_EVERY: Final = timedelta(seconds=2)
_DECK_ID_LENGTH: Final = 12
_BUILD_EVENT: Final = "deck_build.ended"
_STEP_EVENT: Final = "build_step.ended"
_ARTEFACTS_REFUSED_EVENT: Final = "build_artefacts.refused"

_log = logging.getLogger(__name__)


class BuildFault(StrEnum):
    """Why a build or one of its steps stopped, where its `reason` is not a refusal.

    A refusal names a `BuildFailureReason` instead.
    """

    TOOLCHAIN_UNAVAILABLE = "toolchain_unavailable"
    TOOLCHAIN_FAILED = "toolchain_failed"
    TIMED_OUT = "timed_out"
    UNREADABLE_DECK = "unreadable_deck"
    UNACCOUNTABLE = "unaccountable"
    ADDING_UP_TIMED_OUT = "adding_up_timed_out"
    NOT_AS_WRITTEN = "not_as_written"


def deck_id_of(slug: str) -> str:
    """The id a line names a deck by: a talk's folder name is its author's text."""
    return sha256(slug.encode()).hexdigest()[:_DECK_ID_LENGTH]


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
    # The step's one `build_step.ended` line: the runner names why it stopped.
    line: StepTimer


class DeckToolchain(Protocol):
    """Runs one Slidev command over the directories a build works in."""

    @abstractmethod
    def ran(self, step: BuildStep) -> BuildFailure | ToolchainUnavailable | None:
        """Run that step, saying nothing when it did what it was asked to.

        What the toolchain printed travels back with a failure, because the
        deck's page shows it (line 9); a step that was given up on, and one
        that could not be started at all, hand back no words. A toolchain that
        was never asked says so with `ToolchainUnavailable`.
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
    `package.json`, and this is its only reader. JSON this is not,
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
    """The theme names read off the toolchain project's `package.json`.

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
    """Builds decks with the Slidev toolchain into directories under one root.

    The root is the cache of the store every process serves the runs from.
    """

    cache: BuildCache
    toolchain: DeckToolchain
    mirrors: SourceMirrors
    output_megabytes: int
    # The identity of the web's own toolchain project, read once at start: the
    # worker and the host runner build from the same project, so a build records
    # this and the wire carries no identity.
    identity: ToolchainIdentity

    @property
    def builds(self) -> Path:
        """The root every run of the build works and is kept in on this disk."""
        return self.cache.root

    def toolchain_identity(self) -> ToolchainIdentity:
        """The toolchain a build runs under, which the web computed at its start."""
        return self.identity

    def build(
        self, deck: Deck, *, source: Source
    ) -> Artefacts | BuildFailure | ToolchainUnavailable:
        """Build this deck at its commit, or say what came of it instead.

        The talk and the PDF are written under a directory of this build's own
        that nothing points at yet, and the directory the deck delivered from
        before is left where it stands. What the toolchain said about a failure
        travels back with it, because the deck's page shows it (line 9). A run
        that ends any other way than handing back a talk — a failure this
        method returns, or a fault one of its own steps could not turn into
        one — still leaves its directory cleaned up, because a run neither
        delivered nor removed is one this instance can never try again. A
        toolchain that was not there for the talk step is no attempt at the
        deck; one that is gone for the PDF step after a talk that ran is a
        failure without words, so a deck whose PDF never answers does not stall
        every later one.
        """
        place = self._a_place_of_its_own()
        delivered = False
        with StepTimer(
            _BUILD_EVENT,
            _log,
            deck_id=deck_id_of(deck.slug),
            source_id=deck.source_id,
        ) as line:
            try:
                broke = self._what_broke(deck, source=source, place=place, line=line)
                if broke is not None:
                    return broke
                written = place.here / OUTPUT_DIRECTORY
                delivered = True
                return Artefacts(
                    directory=written / TALK_DIRECTORY, pdf=written / PDF_FILE
                )
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
                _log.info(
                    _ARTEFACTS_REFUSED_EVENT,
                    extra={"reason": BuildFault.NOT_AS_WRITTEN},
                )
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

    def deliver(self, artefacts: Artefacts) -> BuildRun | None:
        """Keep the run those artefacts stand in in the store, or answer nothing."""
        name = artefacts.directory.parent.parent.name
        return BuildRun(name) if self.cache.keep(name) else None

    def kept(self, build: Build) -> Artefacts | None:
        """That build's talk and PDF under the root, fetched where they are missing."""
        run = self.cache.local(build.run.name)
        if run is None:
            return None
        written = run / OUTPUT_DIRECTORY
        return Artefacts(directory=written / TALK_DIRECTORY, pdf=written / PDF_FILE)

    def runs(self) -> frozenset[BuildRun] | None:
        """Every run in the store or under the root; nothing where one is unlisted."""
        names = self.cache.names()
        return None if names is None else frozenset(map(BuildRun, names))

    def remove(self, run: BuildRun) -> PurePosixPath | None:
        """Take that run out of the store and off disk, or name it if it stayed."""
        if self.cache.remove(run.name):
            return None
        return PurePosixPath(self.builds.absolute().name, run.name)

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
        line: StepTimer,
    ) -> BuildFailure | ToolchainUnavailable | None:
        """Write the deck out, build the talk, export the PDF, or say what broke."""
        if not self._exported(deck, source=source, into=place.here / DECK_DIRECTORY):
            # A tree this server cannot read out of its own mirror is a fault
            # of this host, not words a deck's author could act on.
            line.fail(BuildFault.UNREADABLE_DECK)
            return BuildFailure.without_words()
        # The walk stops at the first step that failed, so a deck that does not
        # build is never exported either.
        for kind in StepKind:
            step_line = _RememberedStepTimer(
                _STEP_EVENT, _log, deck_id=deck_id_of(deck.slug), step=kind.value
            )
            try:
                with step_line:
                    broke = self._ran(kind, deck=deck, place=place, line=step_line)
            except Exception:
                # The step's line is the exception's one error line.
                line.handled()
                raise
            if isinstance(broke, ToolchainUnavailable) and kind is not StepKind.BUILD:
                broke = BuildFailure.without_words()
            if broke is not None:
                step_line.ends(line)
                return broke
        return self._more_than_it_may_leave(place, line)

    def _ran(
        self,
        kind: StepKind,
        *,
        deck: Deck,
        place: BuildPlace,
        line: StepTimer,
    ) -> BuildFailure | ToolchainUnavailable | None:
        """Run one step of the build under its own line."""
        return self.toolchain.ran(
            BuildStep(
                place=place,
                slug=deck.slug,
                name=kind,
                arguments=slidev_arguments(kind, place=place, slug=deck.slug),
                watched=lambda: self._more_than_it_may_leave(place, line),
                line=line,
            ),
        )

    def _more_than_it_may_leave(
        self,
        place: BuildPlace,
        line: StepTimer,
    ) -> BuildFailure | None:
        """Refuse a build whose talk is more than one build may leave here.

        A deck's own code decides what its talk holds, and a talk this machine
        cannot keep is a machine that stops keeping the others. It is asked
        while the build runs and once more when it is over, because a build
        that fills this machine has filled it long before it ends. No toolchain
        failed, so the reason a page words here is this server's own; what only
        this host can act on is the reason its line carries.
        """
        added = added_up(
            place.here / OUTPUT_DIRECTORY,
            ends_by=monotonic() + _ADDING_UP_WITHIN.total_seconds(),
        )
        line.add(entry_count=added.entries)
        if added.unreadable is not None:
            line.fail(BuildFault.UNACCOUNTABLE)
            return BuildFailure.without_words()
        if not added.in_time:
            line.fail(BuildFault.ADDING_UP_TIMED_OUT)
            return BuildFailure.without_words()
        line.add(written_megabytes=added.megabytes)
        if added.entries > MAXIMUM_ENTRIES:
            line.refuse(BuildFailureReason.OUTPUT_ENTRIES)
            return BuildFailure(
                reason=BuildFailureReason.OUTPUT_ENTRIES,
                allowed_entries=MAXIMUM_ENTRIES,
            )
        if added.megabytes <= self.output_megabytes:
            return None
        line.refuse(BuildFailureReason.OUTPUT_MEGABYTES)
        return BuildFailure(
            reason=BuildFailureReason.OUTPUT_MEGABYTES,
            written_megabytes=added.megabytes,
            allowed_megabytes=self.output_megabytes,
        )

    def _exported(self, deck: Deck, *, source: Source, into: Path) -> bool:
        """Write the deck's tree at its commit out of the mirror, as files."""
        mirror = self.mirrors.of(source)
        if mirror is None:
            return False
        try:
            mirror.export(
                Revision(ref=source.ref, commit=deck.commit),
                source.deck_path(deck.slug),
                into=into,
            )
        except MirrorError:
            return False
        return True


_REFUSALS: Final = frozenset(
    {
        BuildFailureReason.OUTPUT_MEGABYTES,
        BuildFailureReason.OUTPUT_ENTRIES,
        BuildFailureReason.OUTPUT_SHAPE,
        BuildFailureReason.DECK_MEGABYTES,
        BuildFailureReason.DECK_PATHS,
    }
)


class _RememberedStepTimer(StepTimer):
    """A step's line that can hand its ending on to the build's own line."""

    _refusal: str | None = None
    _failure: str | None = None

    def refuse(self, reason: str) -> None:
        """End the step as refused, and remember why."""
        self._refusal = reason
        super().refuse(reason)

    def fail(self, reason: str) -> None:
        """End the step as failed, and remember why."""
        self._failure = reason
        super().fail(reason)

    def ends(self, build: StepTimer) -> None:
        """End the build's line for the very reason this step stopped.

        A failed step has written the one error line of its fault, so the build's
        line is a warning: it names the reason and adds no second error.
        """
        if self._failure is not None:
            build.handled()
            build.fail(self._failure)
        elif self._refusal is not None:
            build.refuse(self._refusal)


def stopped_by(line: StepTimer, broke: BuildFailure) -> None:
    """End a line as what a step handed back says: a refusal or a fault.

    A deck whose own build fails is no fault of this host, so its line is a
    handled warning, not an error.
    """
    if broke.reason in _REFUSALS:
        line.refuse(broke.reason)
    else:
        # What is no refusal is the deck's own failure, with words or none: a
        # host fault wrote its own line before it handed the failure back.
        line.handled()
        line.fail(BuildFault.TOOLCHAIN_FAILED)


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
        except OSError:
            # A toolchain that cannot be started is this host's fault, and what
            # the operating system says about it names paths of this host; the
            # deck's page and the line are told no words, only the reason.
            step.line.fail(BuildFault.TOOLCHAIN_UNAVAILABLE)
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
        step.line.handled()
        step.line.fail(BuildFault.TIMED_OUT)
        # What a step that was killed had printed is not shown: it is the words
        # of a run nobody waited for, and the page says so in its own sentence.
        return BuildFailure.without_words()
    if process.returncode != 0:
        words = failure_text(went.said)
        failure = BuildFailure.said(words)
        stopped_by(step.line, failure)
        return failure
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


def _the_environment_of_this_server() -> dict[str, str]:
    """The whole environment a step's own command line is run with."""
    return {name: os.environ[name] for name in _INHERITED if name in os.environ}
