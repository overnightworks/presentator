"""`presentator unpin-host` lets the next pull pin a host's key again."""

from pathlib import Path

import psycopg
import pytest
from sqlalchemy import Engine

from gitmirror.model import HostKey
from presentator.adapters.postgres.host_keys import PostgresHostKeyPins
from presentator.host import main
from tests.support.log_lines import json_logged
from tests.support.postgres import NO_DATABASE_HERE, point_at

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


def test_unpin_host_of_a_host_nothing_pinned_ends_with_status_one(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    point_at(monkeypatch, database_url, tmp_path)

    with pytest.raises(SystemExit) as ended:
        main.main(["unpin-host", _THE_PINNED_KEY.host])

    assert ended.value.code == 1


def test_unpin_host_against_a_database_that_does_not_answer_ends_in_one_failed_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    point_at(monkeypatch, NO_DATABASE_HERE, tmp_path)

    with json_logged(capsys) as logged, pytest.raises(SystemExit) as ended:
        main.main(["unpin-host", _THE_PINNED_KEY.host])

    assert ended.value.code == 1
    (failed,) = logged.lines
    assert (failed["event"], failed["level"]) == ("process.failed", "error")
    assert (failed["reason"], failed["error_type"]) == (
        "store_unavailable",
        psycopg.OperationalError.__name__,
    )
    assert "127.0.0.1" not in logged.text
    assert not capsys.readouterr().err
