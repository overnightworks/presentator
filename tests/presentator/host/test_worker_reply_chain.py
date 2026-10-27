"""The reply phase through the real roles as child processes."""

import stat
import time
from pathlib import Path

import pytest

from presentator.host import worker_serving
from presentator.host.worker_serving import LifeEnd
from presentator.host.worker_wire import WireExit
from tests.presentator.host.support.controller_life import controller_life
from tests.presentator.host.support.worker_life import (
    PATH,
    LifeScenario,
    run_life,
)
from tests.support import step_support
from tests.support import worker_support as support
from tests.support.worker_chain import SLEEPER, WAIT_SECONDS, ChainSpawn

REPLY_BUDGET_SECONDS = 1.0
DECK_MODE_REPORTER_EXITING_SENT = f"""
import os, stat, sys
mode = stat.S_IMODE(os.stat(sys.argv[3]).st_mode)
os.write(int(sys.argv[1]), str(oct(mode)).encode())
sys.exit({int(WireExit.SENT)})
"""
OUTCOME_THEN_STALL = f"""
import os, sys, time
os.write(int(sys.argv[1]), {support.OUTCOME_FAILED!r})
time.sleep(120)
"""


@pytest.fixture
def sandbox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("PATH", PATH)
    return tmp_path


def test_a_valid_upload_over_a_controller_gets_status_built_talk_entries_end_and_eof(
    volume: Path, tmp_path: Path
) -> None:
    life = controller_life(volume, tmp_path, support.VALID_STEP + support.END)

    assert life.reply == (
        support.STATUS
        + support.OUTCOME_BUILT
        + support.talk_entries(step_support.TALK_INDEX)
    )
    assert life.exit_status == 0
    assert life.roles == ["idle", "upload", "step", "send"]
    assert life.announced_pid == life.step_pid
    assert not life.present_after_exit


def test_the_deck_a_fake_sender_sees_is_0755_and_its_exit_sent_ends_the_life_consumed(
    sandbox: Path,
) -> None:
    deck = sandbox / "deck"
    spawn = ChainSpawn(programs={"send": [DECK_MODE_REPORTER_EXITING_SENT, str(deck)]})

    life = run_life(support.VALID_STEP + support.END, spawn, LifeScenario(work=sandbox))

    assert life.reply == support.STATUS + oct(support.DECK_CLOSED_MODE).encode()
    assert life.life_end == LifeEnd.CONSUMED


def test_an_exit_that_is_not_sent_ends_the_life_wire_exit_with_the_status_only(
    sandbox: Path,
) -> None:
    spawn = ChainSpawn(programs={"send": ["import sys; sys.exit(1)"]})

    life = run_life(support.VALID_STEP + support.END, spawn, LifeScenario(work=sandbox))

    assert life.reply == support.STATUS
    assert life.life_end == LifeEnd.WIRE_EXIT


STALLED_SENDERS = {
    "a sender that stalls": (SLEEPER, support.STATUS),
    "a sender that writes the OUTCOME and stalls": (
        OUTCOME_THEN_STALL,
        support.STATUS + support.OUTCOME_FAILED,
    ),
}


@pytest.mark.parametrize(
    ("program", "reply"), STALLED_SENDERS.values(), ids=STALLED_SENDERS.keys()
)
def test_a_stalled_sender_is_killed_at_the_reply_budget_leaving_the_reply_incomplete(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, program: str, reply: bytes
) -> None:
    monkeypatch.setattr(worker_serving, "REPLY_SECONDS", REPLY_BUDGET_SECONDS)
    spawn = ChainSpawn(programs={"send": [program]})
    deck = sandbox / "deck"

    started = time.monotonic()
    life = run_life(support.VALID_STEP + support.END, spawn, LifeScenario(work=sandbox))
    elapsed = time.monotonic() - started

    assert life.life_end == LifeEnd.REPLY_EXPIRED
    assert life.reply == reply
    assert REPLY_BUDGET_SECONDS <= elapsed < REPLY_BUDGET_SECONDS + WAIT_SECONDS
    assert stat.S_IMODE(deck.stat().st_mode) == support.DECK_CLOSED_MODE
