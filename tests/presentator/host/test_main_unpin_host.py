"""`presentator unpin-host` lets the next pull pin a host's key again."""

from pathlib import Path

import pytest
from sqlalchemy import Engine

from gitmirror.model import HostKey
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins
from presentator.host import main
from presentator.host.command_line import NOTHING_PINNED_STATUS
from tests.support.postgres import point_at

_THE_PINNED_KEY = HostKey(host="git.example", key="ssh-ed25519 AAAAthe-first-key")
_THE_ROTATED_KEY = HostKey(host="git.example", key="ssh-ed25519 AAAAthe-rotated-key")
_ANOTHER_HOSTS_KEY = HostKey(host="other.example", key="ssh-ed25519 AAAAother-key")


def test_unpin_host_removes_that_hosts_pin_so_the_next_pull_pins_again(
    database_url: str,
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    point_at(monkeypatch, database_url, tmp_path)
    pins = PostgresHostKeyPins(engine=engine)
    pins.pin(_THE_PINNED_KEY)
    pins.pin(_ANOTHER_HOSTS_KEY)

    main.main(["unpin-host", _THE_PINNED_KEY.host])

    assert pins.pinned() == (_ANOTHER_HOSTS_KEY,)
    assert pins.pin(_THE_ROTATED_KEY) == _THE_ROTATED_KEY


def test_unpin_host_of_a_host_nothing_pinned_ends_with_its_own_status(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, database_url, tmp_path)

    with pytest.raises(SystemExit) as ended:
        main.main(["unpin-host", _THE_PINNED_KEY.host])

    assert ended.value.code == NOTHING_PINNED_STATUS
