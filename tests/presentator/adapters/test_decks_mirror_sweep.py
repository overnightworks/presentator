"""The turn's sweep of the mirrors directory: what it removes and what it keeps."""

import fcntl
from pathlib import Path

from tests.support.deck_source_support import mirrors_under

# The name an ssh run gives the known_hosts file it is handed, before the
# random part `tempfile` adds.
_A_RUNS_KNOWN_HOSTS = "known_hosts.a1b2c3"


def _a_mirrors_file(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / name
    file.write_text("ssh-stub-host ssh-ed25519 AAAAthe-stub-host\n", encoding="utf-8")
    return file


def test_the_sweep_removes_a_known_hosts_file_no_ssh_run_holds(
    tmp_path: Path,
) -> None:
    mirrors = mirrors_under(tmp_path)
    leftover = _a_mirrors_file(mirrors.directory, _A_RUNS_KNOWN_HOSTS)

    mirrors.remove_all_but([])

    assert not leftover.exists()


def test_the_sweep_keeps_the_known_hosts_file_of_a_running_ssh_run(
    tmp_path: Path,
) -> None:
    mirrors = mirrors_under(tmp_path)
    running = _a_mirrors_file(mirrors.directory, _A_RUNS_KNOWN_HOSTS)

    with running.open("rb") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        mirrors.remove_all_but([])

        assert running.exists()


def test_the_sweep_keeps_the_known_hosts_file_a_start_adopts(
    tmp_path: Path,
) -> None:
    mirrors = mirrors_under(tmp_path)
    earlier = _a_mirrors_file(mirrors.directory, "known_hosts")

    mirrors.remove_all_but([])

    assert earlier.exists()
