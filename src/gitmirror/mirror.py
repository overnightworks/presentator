"""Keeps a bare mirror of one source by driving the `git` executable.

git's own program already owns the transports, the credential helpers, and the
prompt suppression an unattended server needs, so this package spawns it rather
than binding a second git implementation.
"""

import base64
import hashlib
import hmac
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from _hashlib import HASH

from gitmirror.model import (
    Change,
    Connection,
    ConnectionCheck,
    ConnectionState,
    CredentialResolver,
    GitSource,
    GitUnavailableError,
    HostKey,
    HostKeyPins,
    InvalidCredentialError,
    KnownHosts,
    MirrorError,
    Revision,
    TreeEntry,
)

_CREDENTIAL_VARIABLE: Final = "GITMIRROR_CREDENTIAL"
# A remote's tree is data nobody here wrote; an entry with one of these names
# cannot be a file or folder of a deck, and writing it would leave the target.
_UNWRITABLE_ENTRY_NAMES: Final = frozenset({"", ".", "..", ".git"})
# GitHub, GitLab, and every other host that hands out a read-only token accept
# any user name beside it, so an operator whose URL names none still reaches a
# host that insists on one. The caller hands an https URL without a user part,
# so git's request never names one and the helper always answers this name.
CREDENTIAL_USER_NAME: Final = "token"
# git asks a helper for the credentials of an https remote, and answering out
# of the child's environment keeps the secret off the command line and off
# disk. `printf '%s\n'` with a constant format, never `echo`,
# because a shell's `echo` reinterprets a backslash sequence inside the value
# it is given — `dash`'s always does, verified with a real git — which can
# truncate or reshape a token that merely happens to contain one.
_CREDENTIAL_HELPER: Final = (
    "!f() { printf 'username=%s\\n' "
    f"'{CREDENTIAL_USER_NAME}'; "
    f'printf "password=%s\\n" "${_CREDENTIAL_VARIABLE}"; }}; f'
)
# git's line-based credential protocol reads one field per line; a literal CR
# or LF inside a secret would forge a second line no `printf` quoting can
# undo, so the boundary where a resolved secret enters the mirror rejects one
# outright rather than hand git a broken request.
_UNSAFE_CREDENTIAL: Final = "a source's credential cannot be sent to git as one line"
_SSH_CONNECT_SECONDS: Final = 10
# Only what git and ssh need to run; the child inherits nothing else, so no
# machine-wide askpass helper can hang a pull and no machine-wide trace setting
# can write the secret into a file.
_INHERITED: Final = ("PATH", "HOME")
_UNATTENDED: Final = {
    # Nothing here can answer a prompt, so a remote that asks for one has to
    # fail instead of waiting for a terminal that will never come.
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": (
        f"ssh -o BatchMode=yes -o ConnectTimeout={_SSH_CONNECT_SECONDS}"
    ),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
# A deploy key's private half lives on disk only for the length of one fetch:
# a 0700 directory holding one 0600 file, removed in a `finally`
# whatever the fetch did.
_SSH_KEY_DIRECTORY_MODE: Final = 0o700
_SSH_KEY_FILE_MODE: Final = 0o600
_SSH_KEY_FILE_NAME: Final = "id_ed25519"
_KNOWN_HOSTS_PREFIX: Final = "known_hosts."
# ssh names a host it reaches on another port than this one `[host]:port`, and
# hashes that name, with a salt of its own, on a line that starts so.
_SSH_PORT: Final = 22
_HASHED_HOST: Final = "|1|"
_LINE_FIELDS: Final = 3
# ssh's own words when a host answers with another key than the pinned one;
# a run whose newly accepted key lost the pin to another process says the same.
_HOST_KEY_REFUSED: Final = "Host key verification failed."
# git prints exactly what the format asks for, so the commit and the time of
# one change arrive on a single line with this between them.
_LAST_CHANGE: Final = "--format=%H %cI"
_BETWEEN_THEM: Final = " "
_GIT_MISSING: Final = "git is required to mirror a source"
_UNREACHABLE: Final = Connection(state=ConnectionState.UNREACHABLE, revision=None)
_CREDENTIAL_UNRESOLVABLE: Final = Connection(
    state=ConnectionState.CREDENTIAL_UNRESOLVABLE,
    revision=None,
)
_REFUSED_CHECK: Final = ConnectionCheck(
    state=ConnectionState.REFUSED,
    commit=None,
    detail=None,
)
_UNREACHABLE_CHECK: Final = ConnectionCheck(
    state=ConnectionState.UNREACHABLE,
    commit=None,
    detail=None,
)
_HOST_KEY_REFUSED_CHECK: Final = ConnectionCheck(
    state=ConnectionState.FAILED,
    commit=None,
    detail=_HOST_KEY_REFUSED,
)
# git's own words for a login the far end asked for or turned down, lowercased
# so a case git happens to pick never hides the match. A bare 403 is
# deliberately absent: a proxy or a WAF answers that status for reasons that
# have nothing to do with the token, so only a message git itself ties to a
# login counts.
_REFUSED_WORDS: Final = (
    "terminal prompts disabled",
    "authentication failed",
    "invalid username or token",
    "returned error: 401",
    # ssh's own words for a deploy key the far end no longer accepts.
    "permission denied (publickey)",
)
# git's own words for a host that never answered at all — no TCP connection,
# no DNS name, no reply before the timeout above already caught it.
_UNREACHABLE_WORDS: Final = (
    "could not resolve host",
    "failed to connect",
    "connection refused",
    "connection timed out",
)
# Any `scheme://user:pass@` a URL can carry, wherever it stands in the text:
# git reflects a redirect target or the remote's own reply verbatim, so this
# runs over the whole diagnostic, not only the source's own URL.
_USERINFO: Final = re.compile(r"://[^/@]*@")


@dataclass(frozen=True, slots=True, kw_only=True)
class GitMirror:
    """A bare clone of one source, refreshed by pulling and read at a commit."""

    source: GitSource
    directory: Path
    credentials: CredentialResolver
    pull_timeout: timedelta
    known_hosts: KnownHosts

    def connect(self) -> Connection:
        """Pull the source's ref, and say what stopped that when it failed."""
        if self.source.credential is None:
            return self._pull(secret=None)
        secret = self.credentials.resolve(self.source.credential)
        if secret is None:
            return _CREDENTIAL_UNRESOLVABLE
        if not secret:
            # A source stored with no secret offers git no login.
            return self._pull(secret=None)
        if not is_ssh_remote(self.source.url):
            _reject_unsafe_credential(secret)
        return self._pull(secret=secret)

    def entries(self, revision: Revision, *, inside: str = "") -> tuple[TreeEntry, ...]:
        """The names one level below `inside` in the tree at this commit."""
        within = (f"{inside}/",) if inside else ()
        listing = self._run("ls-tree", "-z", revision.commit, "--", *within)
        return tuple(
            _tree_entry(record) for record in os.fsdecode(listing).split("\0") if record
        )

    def read(self, revision: Revision, path: str) -> bytes:
        """The file's bytes at this commit; nothing is ever checked out."""
        return self._run("cat-file", "blob", f"{revision.commit}:{path}")

    def export(self, revision: Revision, path: str, *, into: Path) -> None:
        """Write the tree under that path at this commit into that directory.

        A caller that has to run a tool over the files needs them as files; the
        mirror stays bare, so the tree is written out beside it rather than
        checked out into it. Git carries no empty directory, so a path with
        nothing under it is a path this commit does not carry.
        """
        listed = self._every_entry(revision, path)
        if not listed:
            message = f"{revision.commit} carries no {path}"
            raise MirrorError(message)
        try:
            into.mkdir(parents=True, exist_ok=True)
            for record in listed:
                target = into / record.below
                if record.entry.is_directory:
                    target.mkdir(exist_ok=True)
                else:
                    target.write_bytes(self.read(revision, f"{path}/{record.below}"))
        except OSError as fault:
            message = f"{revision.commit} {path} cannot be written to {into}: {fault}"
            raise MirrorError(message) from fault

    def _every_entry(self, revision: Revision, path: str) -> tuple[_Listed, ...]:
        """Every entry at any depth strictly inside that path, each name checked.

        A tree is listed before what it holds. The listing also names the
        folders on the way down to `path`, which are not part of what is
        exported.
        """
        listing = self._run(
            "ls-tree", "-r", "-t", "-z", revision.commit, "--", f"{path}/"
        )
        inside = f"{path}/"
        return tuple(
            _Listed(below=record_path.removeprefix(inside), entry=entry)
            for record_path, entry in (
                _listed_record(record)
                for record in os.fsdecode(listing).split("\0")
                if record
            )
            if record_path.startswith(inside)
        )

    def last_change(self, revision: Revision, path: str) -> Change:
        """The last commit up to this one that touched that path, and when.

        A caller that asks per path gets a value that stands still while other
        paths move, which is what tells one changed folder from an untouched
        one.
        """
        stamped = self._text("log", "-1", _LAST_CHANGE, revision.commit, "--", path)
        commit, _, at = stamped.partition(_BETWEEN_THEM)
        try:
            return Change(commit=commit, at=datetime.fromisoformat(at))
        except ValueError as unreadable:
            message = f"commit {commit} carries a date no calendar can hold: {at}"
            raise MirrorError(message) from unreadable

    def _pull(self, *, secret: str | None) -> Connection:
        url = self.source.url
        self.directory.parent.mkdir(parents=True, exist_ok=True)
        self._run("init", "--bare", "--quiet")
        ref = self.source.ref
        # The URL may carry a user name, so this is the only call that never
        # turns its command line into an error message.
        try:
            with (
                _credentials(url, secret) as (credential, ssh_key),
                _pinned_hosts(ssh_key, self.known_hosts) as hosts,
            ):
                pulled = subprocess.run(  # noqa: S603 - argv is a list led by the resolved git executable with no shell
                    [
                        _git_executable(),
                        *credential_arguments(credential),
                        "--git-dir",
                        str(self.directory),
                        "fetch",
                        "--no-tags",
                        "--",
                        url,
                        f"+refs/heads/{ref}:refs/heads/{ref}",
                    ],
                    capture_output=True,
                    check=False,
                    env=unattended_environment(
                        credential,
                        ssh_key=ssh_key,
                        known_hosts=None if hosts is None else hosts.path,
                    ),
                    timeout=self.pull_timeout.total_seconds(),
                )
                trusted = hosts is None or hosts.accepted_keys_stand()
        except subprocess.TimeoutExpired:
            # A remote nobody can reach must not hold the page that asked for it.
            return _UNREACHABLE
        if not trusted:
            return Connection(state=ConnectionState.FAILED, revision=None)
        if pulled.returncode != 0:
            stderr = pulled.stderr.decode(errors="replace").strip()
            return Connection(
                state=connection_state_for_failure(
                    stderr, credential_offered=secret is not None
                ),
                revision=None,
            )
        return Connection(
            state=ConnectionState.READY,
            revision=Revision(
                ref=ref,
                commit=self._text("rev-parse", f"refs/heads/{ref}"),
            ),
        )

    def _text(self, *arguments: str) -> str:
        return self._run(*arguments).decode().strip()

    def _run(self, *arguments: str) -> bytes:
        completed = subprocess.run(  # noqa: S603 - argv is a list led by the resolved git executable with no shell
            [
                _git_executable(),
                "--literal-pathspecs",
                "--git-dir",
                str(self.directory),
                *arguments,
            ],
            capture_output=True,
            check=False,
            env=unattended_environment(secret=None),
        )
        if completed.returncode != 0:
            reason = completed.stderr.decode(errors="replace").strip()
            message = f"git {' '.join(arguments)}: {reason}"
            raise MirrorError(message)
        return completed.stdout


def check_connection(
    *,
    url: str,
    ref: str,
    secret: str | None,
    timeout: timedelta,
    known_hosts: KnownHosts | None = None,
) -> ConnectionCheck:
    """Ask the remote for one ref's head, without ever writing a mirror to disk.

    `ls-remote` alone tells an admin whether the server can read the
    repository before Add source stores anything, through the same
    credential helper, environment, and classifier `_pull` uses.
    """
    try:
        if secret is not None and not is_ssh_remote(url):
            _reject_unsafe_credential(secret)
    except InvalidCredentialError:
        return _REFUSED_CHECK
    try:
        with (
            _credentials(url, secret) as (credential, ssh_key),
            _pinned_hosts(ssh_key, known_hosts) as hosts,
        ):
            probed = subprocess.run(  # noqa: S603 - argv is a list led by the resolved git executable with no shell
                [
                    _git_executable(),
                    *credential_arguments(credential),
                    "ls-remote",
                    "--exit-code",
                    "--",
                    url,
                    f"refs/heads/{ref}",
                ],
                capture_output=True,
                check=False,
                env=unattended_environment(
                    credential,
                    ssh_key=ssh_key,
                    known_hosts=None if hosts is None else hosts.path,
                ),
                timeout=timeout.total_seconds(),
            )
            trusted = hosts is None or hosts.accepted_keys_stand()
    except subprocess.TimeoutExpired:
        return _UNREACHABLE_CHECK
    if not trusted:
        return _HOST_KEY_REFUSED_CHECK
    return _check_answered_by(probed, secret=secret)


def _check_answered_by(
    probed: subprocess.CompletedProcess[bytes], *, secret: str | None
) -> ConnectionCheck:
    """What a finished `ls-remote` says: the head commit, or which failure and why."""
    if probed.returncode != 0:
        stderr = probed.stderr.decode(errors="replace").strip()
        sanitized = _sanitized(stderr, secret=secret)
        return ConnectionCheck(
            state=connection_state_for_failure(
                stderr,
                credential_offered=secret is not None,
            ),
            commit=None,
            detail=sanitized.splitlines()[0] if sanitized else None,
        )
    commit, _, _ = probed.stdout.decode().strip().partition("\t")
    return ConnectionCheck(
        state=ConnectionState.READY,
        commit=commit or None,
        detail=None,
    )


def _git_executable() -> str:
    git = shutil.which("git")
    if git is None:
        raise GitUnavailableError(_GIT_MISSING)
    return git


def credential_arguments(secret: str | None) -> tuple[str, ...]:
    """The `-c` options git is run with, carrying no secret value itself."""
    # An empty helper clears whatever a config file or the environment set, so
    # only the helper named here can ever answer.
    cleared = ("-c", "credential.helper=")
    helper = () if secret is None else ("-c", f"credential.helper={_CREDENTIAL_HELPER}")
    return (*cleared, *helper)


def unattended_environment(
    secret: str | None,
    *,
    ssh_key: Path | None = None,
    known_hosts: Path | None = None,
) -> dict[str, str]:
    """The whole environment git runs in, with the secret only when there is one.

    An ssh key file overrides `GIT_SSH_COMMAND` to name it, so a deploy-key
    fetch authenticates through that file rather than through any identity
    the machine's own `ssh` would otherwise offer.
    """
    inherited = {name: os.environ[name] for name in _INHERITED if name in os.environ}
    carried = {} if secret is None else {_CREDENTIAL_VARIABLE: secret}
    environment = {**inherited, **_UNATTENDED, **carried}
    if ssh_key is not None and known_hosts is not None:
        environment["GIT_SSH_COMMAND"] = _ssh_command(ssh_key, known_hosts)
    return environment


def is_ssh_remote(url: str) -> bool:
    """Whether this git remote is reached over ssh: `ssh://` or the scp form."""
    return url.startswith("ssh://") or ("://" not in url and "@" in url)


def _ssh_command(key: Path, known_hosts: Path) -> str:
    """The `ssh` git is told to run: this key alone, host key pinned on first use.

    The host stays readable and ssh adds no further key on its own, so each
    line it writes names one host the pins can be keyed by.
    """
    return (
        f"ssh -i {key} -o IdentitiesOnly=yes -o BatchMode=yes "
        f"-o ConnectTimeout={_SSH_CONNECT_SECONDS} -o StrictHostKeyChecking=accept-new "
        f"-o UserKnownHostsFile={known_hosts} -o HashKnownHosts=no -o UpdateHostKeys=no"
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class _PinnedHostsFile:
    """The known_hosts file of one ssh run, written from the pins it started with."""

    path: Path
    known_hosts: KnownHosts
    pinned: frozenset[HostKey]

    def accepted_keys_stand(self) -> bool:
        """Pin each key ssh accepted during the run; false if another pin stood first.

        Two processes can each accept a new host's key before either pinned
        it, and only the first pin stands, so the other's run is not trusted.
        """
        accepted = _host_keys_in(self.path) - self.pinned
        return all(self.known_hosts.pins.pin(key) == key for key in accepted)


@contextmanager
def _pinned_hosts(
    ssh_key: Path | None, known_hosts: KnownHosts | None
) -> Generator[_PinnedHostsFile | None]:
    """Every pinned host key in a file for the length of one ssh run, else nothing."""
    if ssh_key is None or known_hosts is None:
        yield None
        return
    pinned = frozenset(known_hosts.pins.pinned())
    known_hosts.directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=_KNOWN_HOSTS_PREFIX, dir=known_hosts.directory
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as hosts_file:
            hosts_file.writelines(f"{each.host} {each.key}\n" for each in pinned)
        yield _PinnedHostsFile(path=path, known_hosts=known_hosts, pinned=pinned)
    finally:
        path.unlink(missing_ok=True)


def _host_keys_in(path: Path) -> frozenset[HostKey]:
    """The keys a known_hosts file holds, one per line as `host key`."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(
        HostKey(host=host, key=key.strip())
        for host, _, key in (line.strip().partition(" ") for line in lines)
        if host and not host.startswith("#")
    )


def adopt_known_hosts(file: Path, *, urls: Iterable[str], pins: HostKeyPins) -> None:
    """Pin the key a known_hosts file ssh wrote holds for each ssh host of the urls.

    ssh may have hashed the host names, so a line is matched by each host the
    urls name. A host pinned already keeps its pin.
    """
    if not file.is_file():
        return
    entries = tuple(_known_host_entries(file))
    for host in sorted({host for url in urls if (host := _known_host_of(url))}):
        key = next((key for names, key in entries if _names(names, host)), None)
        if key is not None:
            pins.pin(HostKey(host=host, key=key))


def _known_host_of(url: str) -> str | None:
    """The name ssh gives the host of an ssh url in a known_hosts file."""
    if url.startswith("ssh://"):
        parsed = urlsplit(url)
        try:
            host, port = parsed.hostname, parsed.port
        except ValueError:
            return None
    elif is_ssh_remote(url):
        authority = url.partition("@")[2]
        bracketed = authority.startswith("[")
        host = (
            authority[1 : authority.find("]")]
            if bracketed
            else authority.partition(":")[0]
        ).lower()
        port = None
    else:
        return None
    return host if port in {None, _SSH_PORT} else f"[{host}]:{port}"


def _known_host_entries(file: Path) -> Generator[tuple[str, str]]:
    """Each line's host names and its key, skipping comments and markers."""
    for line in file.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) >= _LINE_FIELDS and not fields[0].startswith(("#", "@")):
            yield fields[0], f"{fields[1]} {fields[2]}"


def _names(names: str, host: str) -> bool:
    """Whether a line's host names, plain or hashed, name that host."""
    if not names.startswith(_HASHED_HOST):
        return host in names.split(",")
    parts = names.removeprefix(_HASHED_HOST).split("|")
    try:
        # A line ssh did not write fails either way: as too many parts or as
        # no base64, whose error is a `ValueError` too.
        salt, digest = (base64.b64decode(part, validate=True) for part in parts)
    except ValueError:
        return False
    hashed = hmac.new(salt, host.encode(), _known_hosts_sha1).digest()
    return hmac.compare_digest(hashed, digest)


def _known_hosts_sha1(data: bytes = b"") -> HASH:
    """SHA-1 as OpenSSH's hashed known_hosts format fixes it.

    The hash only finds the line that names a host; the pinned key, not the
    hash, decides trust, so no security property rests on SHA-1 here.
    """
    return hashlib.sha1(data, usedforsecurity=False)


@contextmanager
def _credentials(
    url: str,
    secret: str | None,
) -> Generator[tuple[str | None, Path | None]]:
    """The secret to hand git's credential helper, and an ssh key file when needed.

    An ssh remote authenticates through the key file alone, never through the
    helper, so the secret travels as a file rather than as the environment
    value the helper would otherwise read.
    """
    if secret is not None and is_ssh_remote(url):
        with _ssh_key_file(secret) as key:
            yield None, key
    else:
        yield secret, None


@contextmanager
def _ssh_key_file(secret: str) -> Generator[Path]:
    """A deploy key's private half, written for the length of one fetch.

    A 0700 directory holding one 0600 file, both gone in the `finally`
    whatever the fetch did with them.
    """
    directory = Path(tempfile.mkdtemp(prefix="gitmirror-deploy-key-"))
    try:
        directory.chmod(_SSH_KEY_DIRECTORY_MODE)
        key = directory / _SSH_KEY_FILE_NAME
        descriptor = os.open(
            key,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            _SSH_KEY_FILE_MODE,
        )
        with os.fdopen(descriptor, "w") as key_file:
            key_file.write(secret)
        yield key
    finally:
        shutil.rmtree(directory)


def _reject_unsafe_credential(secret: str) -> None:
    """Fail loud rather than hand git's line-based protocol a broken line.

    A secret can reach a mirror from any resolver an instance configures, so
    this is the one place it passes through, whatever its own input
    validation does or does not catch.
    """
    if not secret.isprintable():
        raise InvalidCredentialError(_UNSAFE_CREDENTIAL)


def connection_state_for_failure(
    stderr: str,
    *,
    credential_offered: bool,
) -> ConnectionState:
    """Which of the three failure states a failed pull's own words say happened.

    A login demanded of a request that offered none is `unreachable`: the
    far end answers a private repository like a missing one, so the address
    is not reachable without a token. Only an offered credential can be refused.

    Unreachable is only ever one of `_UNREACHABLE_WORDS` or the timeout above;
    an answer git phrases in words this function does not recognise — a TLS
    certificate failure, a proxy's own text — is not proof the host never
    answered, so it stays `failed` rather than guessing.
    """
    lowered = stderr.lower()
    if any(word in lowered for word in _REFUSED_WORDS):
        return (
            ConnectionState.REFUSED
            if credential_offered
            else ConnectionState.UNREACHABLE
        )
    if any(word in lowered for word in _UNREACHABLE_WORDS):
        return ConnectionState.UNREACHABLE
    return ConnectionState.FAILED


def _without_userinfo(text: str) -> str:
    """That text with any embedded user name or password hidden, wherever it stands."""
    return _USERINFO.sub("://***@", text)


def _sanitized(stderr: str, *, secret: str | None) -> str:
    """That stderr with every userinfo and the secret itself erased.

    The secret only ever reaches git through the child's environment, so it
    cannot appear here on its own account; this still erases it, because a
    defence a probe can falsify is not a defence.
    """
    redacted = _without_userinfo(stderr)
    if secret:
        redacted = redacted.replace(secret, "***")
    return redacted


@dataclass(frozen=True, slots=True)
class _Listed:
    """One entry of a recursive listing, and its path below the listed folder."""

    below: str
    entry: TreeEntry


def _tree_entry(record: str) -> TreeEntry:
    return _listed_record(record)[1]


def _listed_record(record: str) -> tuple[str, TreeEntry]:
    attributes, _, path = record.partition("\t")
    _mode, kind, _object = attributes.split()
    # The raw last element, because a path type drops a `.` element and would
    # hide the name that makes a write land in, or above, its own directory.
    parts = path.split("/")
    name = parts[-1]
    # Every part, because one entry whose own name holds slashes is listed as
    # several parts, and a write follows all of them.
    for part in parts:
        if part in _UNWRITABLE_ENTRY_NAMES:
            message = f"the tree carries an entry named {part!r}"
            raise MirrorError(message)
    return path, TreeEntry(name=name, is_directory=kind == "tree")
