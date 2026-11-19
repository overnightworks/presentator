"""Where decks come from: git and TOML.

`gitmirror` owns talking to the remote; this module translates
between its vocabulary and the product's. It holds the mirrors and how a
source's folders are read from them, and the source rows' translation that the
Postgres stores share.
"""

import logging
import shutil
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Final

from logline import StepTimer

from gitmirror.mirror import (
    GitMirror,
    check_connection,
    remove_leftover_known_hosts,
)
from gitmirror.model import (
    ConnectionState,
    CredentialReference,
    CredentialResolver,
    GitSource,
    HostKeyPins,
    InvalidCredentialError,
    KnownHosts,
    MirrorError,
    Revision,
)
from presentator.adapters.deck_manifest import MalformedManifestError, titled_by
from presentator.adapters.persisted import read_persisted
from presentator.contracts.decks import (
    MANIFEST_FILE,
    AccessKind,
    ConnectionCheckResult,
    DeckFolder,
    Source,
    SourcePoll,
    SourceRun,
    SourceRunFailure,
    SourceRunOutcome,
    SourceWrite,
    access_kind_of,
)

_SOURCE_FETCH_ENDED: Final = "source_fetch.ended"
_MANIFEST_REFUSED: Final = "manifest.refused"
_MIRROR_REFUSED: Final = "mirror_removal.refused"
_MIRROR_STAYED: Final = "directory_not_removable"
_MANIFEST_UNREADABLE: Final = "unreadable"
_MIRROR_SUFFIX: Final = ".git"
# `gitmirror`'s own words never reach a `SourceRun`; a connection that carries
# no revision is always one of these states, never `READY`.
_FAILURE_BY_CONNECTION_STATE: Final[dict[ConnectionState, SourceRunFailure]] = {
    ConnectionState.CREDENTIAL_UNRESOLVABLE: SourceRunFailure.CREDENTIAL_UNRESOLVABLE,
    ConnectionState.REFUSED: SourceRunFailure.REFUSED,
    ConnectionState.FAILED: SourceRunFailure.FAILED,
    ConnectionState.UNREACHABLE: SourceRunFailure.UNREACHABLE,
    ConnectionState.HOST_KEY_REFUSED: SourceRunFailure.HOST_KEY_REFUSED,
}

_log = logging.getLogger(__name__)


def _unread(fetch: StepTimer, failure: SourceRunFailure) -> SourcePoll:
    """End the fetch step for a source that could not be read, with its code."""
    if failure.is_refusal:
        fetch.refuse(failure.value)
    else:
        fetch.fail(failure.value)
    return SourcePoll(folders=None, commit=None, failure=failure)


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceMirrors:
    """The bare mirrors this instance keeps, and how one of them is opened.

    Reading a source's folders and building a deck out of one both need the
    same mirror, so where it lives and how it is named is decided once.
    The directory is this process's cache: a mirror is rebuilt from its
    source by the next pull, and the host keys it trusts live in `host_keys`.
    """

    directory: Path
    credentials: CredentialResolver
    pull_timeout: timedelta
    host_keys: HostKeyPins

    @property
    def known_hosts(self) -> KnownHosts:
        """Where every ssh run of this process is handed the pinned host keys."""
        return KnownHosts(directory=self.directory, pins=self.host_keys)

    def of(self, source: Source) -> GitMirror | None:
        """The mirror of that source, or nothing when it no longer resolves.

        An address that names no access is unresolvable before any credential
        is looked up.
        """
        if access_kind_of(source.url) is None:
            return None
        return GitMirror(
            source=GitSource(
                url=source.url,
                ref=source.ref,
                # The source's own id is the anchor, because the row is what
                # holds the secret; the resolver reads it there.
                credential=CredentialReference(name=source.id),
            ),
            directory=self._directory_of(source),
            credentials=self.credentials,
            pull_timeout=self.pull_timeout,
            known_hosts=self.known_hosts,
        )

    def remove(self, source: Source) -> PurePosixPath | None:
        """Delete this source's mirror from disk, and name it if it stayed.

        The directory is named off the address alone (`_directory_of`), so
        this goes whether or not `of` could still open it — a mount that
        vanished must not leave the mirror it once carried behind. A path
        that survives the attempt is answered under the mirrors directory's
        own name and logged by the source's id, never its name or address,
        because a caller that must not delete a source's row while its mirror
        still stands needs to know, not just be told.
        """
        directory = self._directory_of(source)
        shutil.rmtree(directory, ignore_errors=True)
        if directory.exists():
            _log.warning(
                _MIRROR_REFUSED,
                extra={"source_id": source.id, "reason": _MIRROR_STAYED},
            )
            return PurePosixPath(self.directory.absolute().name, directory.name)
        return None

    def remove_all_but(self, sources: Iterable[Source]) -> None:
        """Delete every mirror in the directory that none of these sources has.

        The known_hosts files ssh runs killed midway left behind go too.
        """
        kept = {self._directory_of(source) for source in sources}
        if not self.directory.is_dir():
            return
        remove_leftover_known_hosts(self.directory)
        for directory in self.directory.glob(f"*{_MIRROR_SUFFIX}"):
            if directory not in kept:
                shutil.rmtree(directory, ignore_errors=True)

    def _directory_of(self, source: Source) -> Path:
        """Where this source's mirror stands.

        A URL is not a directory name, and two sources must not share one.
        """
        name = sha256(source.url.encode()).hexdigest()
        return self.directory / f"{name}{_MIRROR_SUFFIX}"


# The access kinds the Add form can check. Everything else is refused before
# a single argument reaches git, never handed to it on the chance a probe
# might make sense of it.
_PROBED_ACCESS_KINDS: Final = frozenset(
    {AccessKind.HTTPS, AccessKind.SSH},
)


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredConnectionChecker:
    """Probes a form's own URL and secret through `ls-remote`, storing nothing."""

    check_timeout: timedelta
    known_hosts: KnownHosts

    def check(self, *, url: str, ref: str, secret: str) -> ConnectionCheckResult:
        """No failure and the head commit when reachable, or which failure and why.

        An access kind the checker does not probe is refused without ever
        reaching `ls-remote`.
        """
        kind = access_kind_of(url)
        if kind not in _PROBED_ACCESS_KINDS:
            return ConnectionCheckResult(
                failure=SourceRunFailure.REFUSED, commit=None, detail=None
            )
        probed = check_connection(
            url=url,
            ref=ref,
            secret=secret or None,
            timeout=self.check_timeout,
            known_hosts=self.known_hosts,
        )
        if probed.state is ConnectionState.READY:
            return ConnectionCheckResult(
                failure=None,
                commit=probed.commit,
                detail=None,
            )
        return ConnectionCheckResult(
            failure=_FAILURE_BY_CONNECTION_STATE[probed.state],
            commit=None,
            detail=probed.detail,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MirroredDeckFolders:
    """Reads a source's folders out of a local bare mirror of its repository."""

    mirrors: SourceMirrors

    def forget(self, source: Source) -> PurePosixPath | None:
        """Delete this source's mirror from disk, and name it if it stayed."""
        return self.mirrors.remove(source)

    def forget_all_but(self, sources: Iterable[Source]) -> None:
        """Delete every mirror on this disk that none of these sources has."""
        self.mirrors.remove_all_but(sources)

    def folders(self, source: Source) -> SourcePoll:
        """Poll for every top-level folder at the newest commit, or why not.

        The reading is one `source_fetch.ended` step: it is a warning where the
        source could not be read, an info line where the read was refused,
        and either way the refresh goes on to the next one.
        """
        with StepTimer(_SOURCE_FETCH_ENDED, _log, source_id=source.id) as fetch:
            fetch.handled()
            try:
                return self._read(source, fetch)
            except InvalidCredentialError:
                failure = SourceRunFailure.CREDENTIAL_UNRESOLVABLE
            except MirrorError:
                failure = SourceRunFailure.FAILED
            return _unread(fetch, failure)

    def _read(self, source: Source, fetch: StepTimer) -> SourcePoll:
        mirror = self.mirrors.of(source)
        if mirror is None:
            return _unread(fetch, SourceRunFailure.FAILED)
        connection = mirror.connect()
        revision = connection.revision
        if revision is None:
            return _unread(fetch, _FAILURE_BY_CONNECTION_STATE[connection.state])
        folders = tuple(
            self._folder(mirror, revision, source, entry.name)
            for entry in mirror.entries(revision, inside=source.folder)
            if entry.is_directory
        )
        unreadable_manifests = sum(
            1
            for folder in folders
            if MANIFEST_FILE in folder.file_names and folder.title is None
        )
        if unreadable_manifests:
            _log.info(
                _MANIFEST_REFUSED,
                extra={
                    "source_id": source.id,
                    "reason": _MANIFEST_UNREADABLE,
                    "folder_count": unreadable_manifests,
                },
            )
        return SourcePoll(folders=folders, commit=revision.commit, failure=None)

    def _folder(
        self,
        mirror: GitMirror,
        revision: Revision,
        source: Source,
        name: str,
    ) -> DeckFolder:
        """The folder as it stands, without a title when its manifest is unreadable.

        A folder nobody can read a title from is still a folder that is there,
        so it must not be reported as gone; the row that says why it carries no
        title belongs to the build states.
        """
        path = source.deck_path(name)
        file_names = frozenset(
            entry.name
            for entry in mirror.entries(revision, inside=path)
            if not entry.is_directory
        )
        # The folder's own last commit, not the source's: a push that touched
        # another folder must leave this deck where it stands (line 19).
        changed = mirror.last_change(revision, path)
        return DeckFolder(
            name=name,
            file_names=file_names,
            title=self._read_title(mirror, revision, source, name, file_names),
            changed_at=changed.at,
            commit=changed.commit,
        )

    def _read_title(
        self,
        mirror: GitMirror,
        revision: Revision,
        source: Source,
        name: str,
        file_names: frozenset[str],
    ) -> str | None:
        """What the folder's manifest names, or nothing when it names nothing."""
        if MANIFEST_FILE not in file_names:
            return None
        try:
            return titled_by(
                mirror.read(revision, f"{source.deck_path(name)}/{MANIFEST_FILE}"),
                folder=name,
            )
        except MalformedManifestError, MirrorError:
            return None


def written_source(
    identifier: str, write: SourceWrite, *, public_key: str | None
) -> Source:
    """The source a write became under that identifier."""
    return Source(
        id=identifier,
        name=write.name,
        url=write.url,
        ref=write.ref,
        owner_id=write.owner_id,
        public_key=public_key,
        folder=write.folder,
    )


def stored_source(row: Sequence[Any]) -> Source:
    """The source of one row read as id, name, URL, ref, owner, public key, folder."""
    identifier, name, url, ref, owner_id, public_key, folder = row
    return Source(
        id=identifier,
        name=name,
        url=url,
        ref=ref,
        owner_id=owner_id,
        public_key=public_key,
        folder=folder,
    )


def stored_source_run(
    source_id: str,
    at: datetime,
    outcome: str,
    commit: str | None,
    reason: str | None,
) -> SourceRun:
    """The run of one row's columns, its words read as this product's enums.

    A run a newer release worded reads as a poll that failed for no named reason.
    """
    known_outcome = read_persisted(SourceRunOutcome, outcome, unknown=None)
    if known_outcome is None:
        return SourceRun(
            source_id=source_id,
            at=at,
            outcome=SourceRunOutcome.FAILURE,
            commit=None,
            reason=SourceRunFailure.FAILED,
        )
    return SourceRun(
        source_id=source_id,
        at=at,
        outcome=known_outcome,
        commit=commit,
        reason=(
            None
            if reason is None
            else read_persisted(
                SourceRunFailure, reason, unknown=SourceRunFailure.FAILED
            )
        ),
    )
