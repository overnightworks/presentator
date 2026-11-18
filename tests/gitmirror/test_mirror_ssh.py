"""A mirror and a check over ssh: the deploy key file and the pinned host keys."""

import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from gitmirror import mirror as mirror_module
from gitmirror.mirror import adopt_known_hosts, check_connection
from gitmirror.model import (
    ConnectionState,
    CredentialReference,
    HostKey,
    HostKeyPins,
    KnownHosts,
)
from presentator.adapters.secrets import generate_deploy_key
from tests.support.git_remote import MAIN_BRANCH, GitRemote, with_the_program
from tests.support.mirror_support import (
    A_GENEROUS_BOUND,
    FixedSecret,
    PinsInMemory,
    a_mirror,
    known_hosts_under,
    written_by_ssh,
)

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
_TOKEN_REFERENCE = CredentialReference(name="A_DEPLOY_KEY")
_THE_HOST_KEY = HostKey(host="ssh-stub-host", key="ssh-ed25519 AAAAthe-stub-host")
_ANOTHER_HOST_KEY = HostKey(host="ssh-stub-host", key="ssh-ed25519 AAAAanother-host")
_A_DECK = {"hello-deck/slides.md": "# Hello\n"}


# The stub answers with the host key in `host-key` and checks it as
# `accept-new` does: a host the known_hosts file names with another key is
# refused, an unnamed one is added. It then consumes every `-i key` /
# `-o value` pair `_ssh_command` builds, records the key path and its mode for
# the test to read back, and runs the real `git-upload-pack` locally — exactly
# what git would have asked the real `ssh` to run on the far end, against a
# bare repository already on this disk.
_STUB_SSH = """#!/bin/sh
key=""
printf '%s' "$*" > "{recorded}/arguments"
for argument; do
  case "$argument" in UserKnownHostsFile=*) hosts=${{argument#*=}} ;; esac
done
presented=$(cat "{recorded}/host-key")
pinned=$(sed -n "s/^{host} //p" "$hosts")
if [ -n "$pinned" ] && [ "$pinned" != "$presented" ]; then
  echo "Host key verification failed." >&2
  exit 255
fi
[ -n "$pinned" ] || printf '%s %s\\n' "{host}" "$presented" >> "$hosts"
while [ "$#" -gt 0 ]; do
  case "$1" in
    -i) key="$2"; shift 2 ;;
    -o) shift 2 ;;
    *) break ;;
  esac
done
printf '%s' "$key" > "{recorded}/key-path"
stat -c %a "$key" > "{recorded}/key-mode"
stat -c %a "$(dirname "$key")" > "{recorded}/key-directory-mode"
shift
exec sh -c "$*"
"""


def ssh_stub_transport(
    monkeypatch: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
) -> tuple[str, Path]:
    """Install the local ssh transport and return its remote URL and record."""
    return f"git@{_THE_HOST_KEY.host}:{remote.bare}", ssh_stub(monkeypatch, tmp_path)


def ssh_stub(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Install the local ssh, answering with the stub host's key; return its record."""
    recorded = tmp_path / "recorded"
    recorded.mkdir()
    (recorded / "host-key").write_text(_THE_HOST_KEY.key, encoding="utf-8")
    with_the_program(
        monkeypatch,
        tmp_path,
        "ssh",
        _STUB_SSH.format(recorded=recorded, host=_THE_HOST_KEY.host),
    )
    return recorded


def assert_ssh_deploy_key_transport(recorded: Path, *, known_hosts: Path) -> None:
    """Assert the observable key-file and SSH invocation contract."""
    assert (recorded / "key-mode").read_text().strip() == "600"
    assert (recorded / "key-directory-mode").read_text().strip() == "700"
    arguments = shlex.split((recorded / "arguments").read_text())
    assert "IdentitiesOnly=yes" in arguments
    assert "BatchMode=yes" in arguments
    assert "ConnectTimeout=10" in arguments
    assert "StrictHostKeyChecking=accept-new" in arguments
    assert "HashKnownHosts=no" in arguments
    assert "UpdateHostKeys=no" in arguments
    [hosts_file] = [
        Path(each.removeprefix("UserKnownHostsFile="))
        for each in arguments
        if each.startswith("UserKnownHostsFile=")
    ]
    assert hosts_file.parent == known_hosts
    assert not hosts_file.exists()
    key_path = Path((recorded / "key-path").read_text())
    assert not key_path.exists()
    assert not key_path.parent.exists()


def test_an_ssh_fetch_uses_a_0600_key_file_removed_after(
    monkeypatch: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit(_A_DECK, at=_PUSHED_AT)
    url, recorded = ssh_stub_transport(monkeypatch, remote, tmp_path)
    private_key = generate_deploy_key().private_key
    mirror = a_mirror(
        url,
        directory=tmp_path / "ssh-mirror",
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(private_key),
    )

    connection = mirror.connect()

    assert connection.state is ConnectionState.READY
    assert connection.revision is not None
    assert connection.revision.commit == remote.head
    assert_ssh_deploy_key_transport(recorded, known_hosts=tmp_path / "ssh-mirror")


def test_an_ssh_check_uses_a_real_key_and_configured_transport(
    monkeypatch: pytest.MonkeyPatch,
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit(_A_DECK, at=_PUSHED_AT)
    url, recorded = ssh_stub_transport(monkeypatch, remote, tmp_path)
    private_key = generate_deploy_key().private_key
    assert private_key.count("\n") > 1
    known_hosts = tmp_path / "instance-known-hosts"

    connection = check_connection(
        url=url,
        ref=MAIN_BRANCH,
        secret=private_key,
        timeout=A_GENEROUS_BOUND,
        known_hosts=known_hosts_under(known_hosts),
    )

    assert connection.state is ConnectionState.READY
    assert connection.commit == remote.head
    assert_ssh_deploy_key_transport(recorded, known_hosts=known_hosts)


@dataclass(frozen=True, slots=True)
class PinnedElsewhereMeanwhile:
    """Pins that held nothing at the start and another key once asked to pin."""

    def pinned(self) -> tuple[HostKey, ...]:
        return ()

    def pin(self, key: HostKey) -> HostKey:
        return _ANOTHER_HOST_KEY


def _connected_over_ssh(
    remote: GitRemote, tmp_path: Path, url: str, pins: HostKeyPins
) -> ConnectionState:
    remote.commit(_A_DECK, at=_PUSHED_AT)
    mirror = a_mirror(
        url,
        directory=tmp_path / "ssh-mirror",
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(generate_deploy_key().private_key),
    )
    known_hosts = KnownHosts(directory=tmp_path / "ssh-mirror", pins=pins)
    return replace(mirror, known_hosts=known_hosts).connect().state


def _checked_over_ssh(
    remote: GitRemote, tmp_path: Path, url: str, pins: HostKeyPins
) -> ConnectionState:
    remote.commit(_A_DECK, at=_PUSHED_AT)
    return check_connection(
        url=url,
        ref=MAIN_BRANCH,
        secret=generate_deploy_key().private_key,
        timeout=A_GENEROUS_BOUND,
        known_hosts=KnownHosts(directory=tmp_path / "hosts", pins=pins),
    ).state


_OVER_SSH = pytest.mark.parametrize(
    "reach", [_connected_over_ssh, _checked_over_ssh], ids=["pull", "check"]
)
Reach = Callable[[GitRemote, Path, str, HostKeyPins], ConnectionState]


@_OVER_SSH
def test_the_host_key_ssh_accepts_on_first_use_is_pinned(
    monkeypatch: pytest.MonkeyPatch, remote: GitRemote, tmp_path: Path, reach: Reach
) -> None:
    url, _ = ssh_stub_transport(monkeypatch, remote, tmp_path)
    pins = PinsInMemory()

    state = reach(remote, tmp_path, url, pins)

    assert state is ConnectionState.READY
    assert pins.pinned() == (_THE_HOST_KEY,)


@_OVER_SSH
def test_a_host_that_answers_with_another_key_than_its_pin_is_refused(
    monkeypatch: pytest.MonkeyPatch, remote: GitRemote, tmp_path: Path, reach: Reach
) -> None:
    url, _ = ssh_stub_transport(monkeypatch, remote, tmp_path)
    pins = PinsInMemory({_ANOTHER_HOST_KEY.host: _ANOTHER_HOST_KEY.key})

    state = reach(remote, tmp_path, url, pins)

    assert state is ConnectionState.FAILED
    assert pins.pinned() == (_ANOTHER_HOST_KEY,)


@_OVER_SSH
def test_a_run_whose_accepted_key_lost_the_pin_to_another_is_not_trusted(
    monkeypatch: pytest.MonkeyPatch, remote: GitRemote, tmp_path: Path, reach: Reach
) -> None:
    url, _ = ssh_stub_transport(monkeypatch, remote, tmp_path)

    state = reach(remote, tmp_path, url, PinnedElsewhereMeanwhile())

    assert state is ConnectionState.FAILED


_A_KEPT_LINE = f"{_THE_HOST_KEY.host} {_THE_HOST_KEY.key}\n"
_A_SCP_ADDRESS = "git@SSH-Stub-Host:talks.git"


@pytest.mark.parametrize(
    ("url", "lines", "hashed", "adopted"),
    [
        (_A_SCP_ADDRESS, _A_KEPT_LINE, False, _THE_HOST_KEY),
        (_A_SCP_ADDRESS, _A_KEPT_LINE, True, _THE_HOST_KEY),
        (
            "ssh://git@ssh-stub-host:2222/talks.git",
            f"[ssh-stub-host]:2222 {_THE_HOST_KEY.key}\n",
            True,
            HostKey(host="[ssh-stub-host]:2222", key=_THE_HOST_KEY.key),
        ),
        (
            "git@[::1]:talks.git",
            f"::1 {_THE_HOST_KEY.key}\n",
            True,
            HostKey(host="::1", key=_THE_HOST_KEY.key),
        ),
        ("ssh://git@another-host/talks.git", _A_KEPT_LINE, True, None),
        ("ssh://git@ssh-stub-host:99999/talks.git", _A_KEPT_LINE, False, None),
        ("https://ssh-stub-host/talks.git", _A_KEPT_LINE, False, None),
        (
            _A_SCP_ADDRESS,
            (
                f"# {_A_KEPT_LINE}|1|no-base64|x {_THE_HOST_KEY.key}\n"
                f"|1|a|b|c {_THE_HOST_KEY.key}\n{_THE_HOST_KEY.host}\n"
            ),
            False,
            None,
        ),
    ],
    ids=[
        "scp-address",
        "hashed",
        "another-port",
        "ipv6",
        "another-host",
        "no-port",
        "https",
        "no-line-ssh-wrote",
    ],
)
def test_a_key_ssh_kept_in_a_known_hosts_file_is_pinned_for_the_host_it_names(
    tmp_path: Path, url: str, lines: str, adopted: HostKey | None, *, hashed: bool
) -> None:
    file = written_by_ssh(tmp_path / "known_hosts", lines, hashed=hashed)
    pins = PinsInMemory()

    adopt_known_hosts(file, urls=[url], pins=pins)

    assert pins.pinned() == (() if adopted is None else (adopted,))


@pytest.mark.parametrize("outcome", ["refusal", "timeout", "exception"])
def test_an_ssh_key_file_is_removed_after_every_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    outcome: str,
) -> None:
    captured: list[Path] = []

    def fetch_failure(
        arguments: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if "fetch" not in arguments:
            return subprocess.CompletedProcess(arguments, 0, stdout=b"")
        environment = cast("dict[str, str]", kwargs["env"])
        captured.append(Path(shlex.split(environment["GIT_SSH_COMMAND"])[2]))
        if outcome == "refusal":
            return subprocess.CompletedProcess(
                arguments,
                1,
                stderr=b"Permission denied (publickey)",
            )
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(arguments, 1)
        message = "subprocess failed"
        raise OSError(message)

    monkeypatch.setattr(mirror_module.subprocess, "run", fetch_failure)
    mirror = a_mirror(
        "git@ssh-stub-host:talks.git",
        directory=tmp_path,
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(generate_deploy_key().private_key),
    )

    if outcome == "exception":
        with pytest.raises(OSError, match="subprocess failed"):
            mirror.connect()
    else:
        connection = mirror.connect()
        assert connection.revision is None

    [key_path] = captured
    assert not key_path.exists()
    assert not key_path.parent.exists()


def test_an_ssh_key_directory_is_removed_when_setting_its_mode_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "deploy-key-directory"

    def make_directory(*_args: object, **_kwargs: object) -> str:
        directory.mkdir()
        return str(directory)

    def refuse_mode(*_args: object, **_kwargs: object) -> None:
        message = "mode denied"
        raise OSError(message)

    monkeypatch.setattr(mirror_module.tempfile, "mkdtemp", make_directory)
    monkeypatch.setattr(Path, "chmod", refuse_mode)

    mirror = a_mirror(
        "git@ssh-stub-host:talks.git",
        directory=tmp_path / "mirror",
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(generate_deploy_key().private_key),
    )

    with pytest.raises(OSError, match="mode denied"):
        mirror.connect()

    assert not directory.exists()


def test_an_ssh_key_directory_setup_failure_surfaces_without_cleanup_masking_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse_directory(*_args: object, **_kwargs: object) -> str:
        message = "directory unavailable"
        raise OSError(message)

    def refuse_cleanup(*_args: object, **_kwargs: object) -> None:
        message = "cleanup must not mask setup"
        raise OSError(message)

    monkeypatch.setattr(mirror_module.tempfile, "mkdtemp", refuse_directory)
    monkeypatch.setattr(mirror_module.shutil, "rmtree", refuse_cleanup)
    mirror = a_mirror(
        "git@ssh-stub-host:talks.git",
        directory=tmp_path / "mirror",
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(generate_deploy_key().private_key),
    )

    with pytest.raises(OSError, match="directory unavailable"):
        mirror.connect()


def test_an_ssh_key_cleanup_failure_is_surfaced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse_cleanup(*_args: object, **_kwargs: object) -> None:
        message = "cleanup denied"
        raise OSError(message)

    recorded = ssh_stub(monkeypatch, tmp_path)
    remove_directory = shutil.rmtree
    monkeypatch.setattr(mirror_module.shutil, "rmtree", refuse_cleanup)
    mirror = a_mirror(
        "git@ssh-stub-host:talks.git",
        directory=tmp_path / "mirror",
        credential=_TOKEN_REFERENCE,
        resolver=FixedSecret(generate_deploy_key().private_key),
    )

    with pytest.raises(OSError, match="cleanup denied"):
        mirror.connect()

    key_path = Path((recorded / "key-path").read_text())
    directory = key_path.parent
    remove_directory(directory)
