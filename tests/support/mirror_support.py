"""A mirror over a real source and the resolvers that stand in for the secrets."""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from gitmirror.mirror import GitMirror
from gitmirror.model import CredentialReference, GitSource, HostKey, KnownHosts
from tests.support.git_remote import MAIN_BRANCH

A_GENEROUS_BOUND = timedelta(seconds=30)
WHAT_THE_RESOLVER_ANSWERS = "what only this test made up"


class NoSecretAnywhere:
    """A resolver for a reference that leads nowhere."""

    def resolve(self, reference: CredentialReference) -> str | None:
        return None


class OneKnownSecret:
    """A resolver that answers whatever reference the source names."""

    def resolve(self, reference: CredentialReference) -> str | None:
        return f"{WHAT_THE_RESOLVER_ANSWERS} for {reference.name}"


@dataclass(slots=True)
class FixedSecret:
    """A resolver that answers one exact value, whatever the reference names."""

    value: str

    def resolve(self, reference: CredentialReference) -> str | None:
        return self.value


@dataclass(slots=True)
class PinsInMemory:
    """Host-key pins one test keeps, the first pin of a host standing."""

    keys: dict[str, str] = field(default_factory=dict[str, str])

    def pinned(self) -> tuple[HostKey, ...]:
        return tuple(HostKey(host=host, key=key) for host, key in self.keys.items())

    def pin(self, key: HostKey) -> HostKey:
        return HostKey(host=key.host, key=self.keys.setdefault(key.host, key.key))


def known_hosts_under(directory: Path) -> KnownHosts:
    """Pins of this test alone, handed to ssh from that directory."""
    return KnownHosts(directory=directory, pins=PinsInMemory())


def a_mirror(
    url: str,
    *,
    directory: Path,
    credential: CredentialReference | None = None,
    resolver: NoSecretAnywhere | OneKnownSecret | FixedSecret | None = None,
    pull_timeout: timedelta = A_GENEROUS_BOUND,
) -> GitMirror:
    return GitMirror(
        source=GitSource(url=url, ref=MAIN_BRANCH, credential=credential),
        directory=directory / "mirror.git",
        credentials=NoSecretAnywhere() if resolver is None else resolver,
        pull_timeout=pull_timeout,
        known_hosts=known_hosts_under(directory),
    )
