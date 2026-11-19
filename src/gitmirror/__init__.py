"""Mirrors any git remote read-only, for callers that never write back.

An independent package beside the product's layers: it knows about
git and credentials, and nothing about presentations.
"""

from gitmirror.mirror import GitMirror, check_connection
from gitmirror.model import (
    Change,
    Connection,
    ConnectionCheck,
    ConnectionState,
    CredentialReference,
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

__all__ = (
    "Change",
    "Connection",
    "ConnectionCheck",
    "ConnectionState",
    "CredentialReference",
    "CredentialResolver",
    "GitMirror",
    "GitSource",
    "GitUnavailableError",
    "HostKey",
    "HostKeyPins",
    "InvalidCredentialError",
    "KnownHosts",
    "MirrorError",
    "Revision",
    "TreeEntry",
    "check_connection",
)
