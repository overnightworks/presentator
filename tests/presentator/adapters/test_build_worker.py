"""The web's client of the build worker, against a fake worker on a real socket."""

import errno
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from presentator.adapters.build_wire import (
    MAXIMUM_ENTRIES,
    MEGABYTE,
    StepHeader,
    read_request,
    send_status,
)
from presentator.adapters.build_worker import Fault
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    OUTPUT_DIRECTORY,
    PDF_FILE,
    TALK_DIRECTORY,
    BuildFailure,
    BuildFailureReason,
    StepKind,
    ToolchainUnavailable,
)
from tests.presentator.adapters.support.build_worker_support import (
    BUILT_BY_STEP,
    BUILT_TALK,
    INDEX,
    MEGABYTES,
    PDF,
    WINDOW,
    WITHOUT_A_REASON,
    MovedClock,
    Script,
    Seen,
    a_step,
    answering,
    ended_line,
    fake_worker,
    proven,
    staging_of,
    toolchain,
    tree_of,
)
from tests.support import worker_support as support
from tests.support.log_lines import json_logged

PROMPTLY = 10
UNAVAILABLE = ToolchainUnavailable()


def nothing_to_reply(_connection: socket.socket, _step: StepHeader) -> None:
    return None


def replying(by_step: dict[StepKind, bytes]) -> Script:
    def reply(connection: socket.socket, step: StepHeader) -> None:
        connection.sendall(by_step[step.step])

    return answering(reply)


def closing_first(count: int, then: Script) -> Script:
    """A worker that closes its first connections unspoken, as a busy one does."""
    remaining = [count]

    def serve(connection: socket.socket, seen: Seen, uploads: Path) -> None:
        if remaining[0]:
            remaining[0] -= 1
            return
        then(connection, seen, uploads)

    return serve


def silent_first(count: int, then: Script) -> Script:
    """A worker that holds its first connections silent until the peer leaves."""
    remaining = [count]

    def serve(connection: socket.socket, seen: Seen, uploads: Path) -> None:
        if remaining[0]:
            remaining[0] -= 1
            connection.settimeout(PROMPTLY)
            connection.recv(1)
            return
        then(connection, seen, uploads)

    return serve


def test_a_worker_that_is_not_there_is_retried_until_the_window_ends(
    volume: Path, tmp_path: Path
) -> None:
    started = time.monotonic()

    result = toolchain(volume).ran(a_step(tmp_path))

    assert result == UNAVAILABLE
    assert WINDOW <= time.monotonic() - started < WINDOW + 0.5


def test_a_worker_that_keeps_closing_is_retried_until_the_window_ends(
    volume: Path, tmp_path: Path
) -> None:
    keeps_closing = closing_first(50, answering(nothing_to_reply))
    started = time.monotonic()

    with fake_worker(volume, keeps_closing, accepts=50):
        result = toolchain(volume).ran(a_step(tmp_path))

    assert result == UNAVAILABLE
    assert WINDOW <= time.monotonic() - started < WINDOW + 0.5


def a_peer_speaking_another_wire(
    connection: socket.socket, _seen: Seen, _uploads: Path
) -> None:
    read_request(connection)
    connection.sendall(b"nonsense")


def a_peer_without_the_key() -> Script:
    return answering(nothing_to_reply, key=b"x" * 32)


@pytest.mark.parametrize(
    ("script", "fault"),
    [
        pytest.param(None, Fault.DEADLINE, id="no worker within the window"),
        pytest.param(
            closing_first(50, answering(nothing_to_reply)),
            Fault.DEADLINE,
            id="a worker closing before its hello",
        ),
        pytest.param(
            proven(a_peer_speaking_another_wire),
            Fault.REPLY_GRAMMAR,
            id="another wire after the hello",
        ),
        pytest.param(
            a_peer_without_the_key(), Fault.NOT_THE_WORKER, id="a peer without the key"
        ),
    ],
)
def test_a_step_the_worker_never_answered_is_unavailable_and_names_the_fault(
    volume: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    script: Script | None,
    fault: Fault,
) -> None:
    """Nothing before the STATUS says anything about a deck."""
    step = a_step(tmp_path)

    with json_logged(capsys) as logged:
        if script is None:
            result = toolchain(volume).ran(step)
        else:
            with fake_worker(volume, script, accepts=50):
                result = toolchain(volume).ran(step)
        line = ended_line(step, logged)

    assert result == UNAVAILABLE
    assert (line["outcome"], line["reason"]) == ("failed", fault.value)


def test_a_worker_closing_after_the_status_is_logged_as_the_connection_ending(
    volume: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    step = a_step(tmp_path)
    with (
        json_logged(capsys) as logged,
        fake_worker(volume, answering(nothing_to_reply), accepts=1),
    ):
        toolchain(volume).ran(step)
        line = ended_line(step, logged)

    assert (line["outcome"], line["reason"]) == ("failed", Fault.CONNECTION.value)


def test_a_peer_that_cannot_prove_the_key_is_refused_without_a_retry_or_a_byte(
    volume: Path, tmp_path: Path
) -> None:
    started = time.monotonic()

    with fake_worker(volume, a_peer_without_the_key(), accepts=50) as seen:
        result = toolchain(volume, connect_window_seconds=30.0).ran(a_step(tmp_path))

    assert result == UNAVAILABLE
    assert time.monotonic() - started < PROMPTLY
    assert seen.steps == []


@pytest.mark.parametrize("closes", [1, 3])
def test_a_worker_closing_then_answering_serves_build_and_export(
    volume: Path, tmp_path: Path, closes: int
) -> None:
    """The next build succeeds once the worker's life has ended."""
    script = closing_first(closes, replying(BUILT_BY_STEP))
    with fake_worker(volume, script, accepts=closes + 3):
        client = toolchain(volume, connect_window_seconds=5.0)

        built = client.ran(a_step(tmp_path))
        exported = client.ran(a_step(tmp_path, StepKind.EXPORT))

    out = tmp_path / "builds" / "run" / OUTPUT_DIRECTORY
    assert (built, exported) == (None, None)
    assert (out / TALK_DIRECTORY / "index.html").read_bytes() == INDEX
    assert (out / PDF_FILE).read_bytes() == PDF


def test_a_silent_connection_is_left_after_its_attempt_for_the_next_within_the_window(
    volume: Path, tmp_path: Path
) -> None:
    """The Service may route an attempt to a pod that has ended and answers nothing."""
    script = silent_first(1, replying(BUILT_BY_STEP))
    with fake_worker(volume, script, accepts=5):
        client = toolchain(volume, connect_window_seconds=5.0, attempt_seconds=0.2)

        built = client.ran(a_step(tmp_path))

    assert built is None


def test_a_reply_dripped_past_the_last_deadline_fails_at_it(
    volume: Path, tmp_path: Path
) -> None:
    """Cell B4: the reply comes at once, but the clock passes the deadline in it."""
    clock = MovedClock()
    client = toolchain(volume, reply_bound_seconds=0.3, clock=clock)
    last_deadline = (
        client.connect_window_seconds
        + client.reply_bound_seconds
        + client.reply_margin_seconds
    )

    def passing_the_deadline(connection: socket.socket, _step: StepHeader) -> None:
        connection.sendall(BUILT_TALK[:1])
        clock.seconds = last_deadline
        connection.sendall(BUILT_TALK[1:])

    with fake_worker(volume, answering(passing_the_deadline)):
        result = client.ran(a_step(tmp_path))

    assert result == WITHOUT_A_REASON


def test_a_status_later_than_the_window_fails_at_it_with_no_upload_byte_sent(
    volume: Path, tmp_path: Path
) -> None:
    """Cell B22: the STEP goes out after closings, and its STATUS comes after W."""
    clock = MovedClock()
    client = toolchain(volume, clock=clock)
    seen_late: list[bytes] = []

    def late_status(connection: socket.socket, _seen: Seen, _uploads: Path) -> None:
        read_request(connection)
        clock.seconds = client.connect_window_seconds
        send_status(connection)
        connection.settimeout(PROMPTLY)
        try:
            seen_late.append(connection.recv(1 << 16))
        except ConnectionResetError:
            # A client that gives up leaves the STATUS unread; its close resets.
            seen_late.append(b"")

    after_three_closings = closing_first(3, proven(late_status))
    with fake_worker(volume, after_three_closings):
        result = client.ran(a_step(tmp_path))

    assert result == UNAVAILABLE
    assert seen_late == [b""]


def test_a_deck_with_node_modules_uploads_only_the_nested_one(
    volume: Path, tmp_path: Path
) -> None:
    """Cell B11."""
    step = a_step(tmp_path)
    deck = step.place.here / DECK_DIRECTORY
    (deck / "node_modules").mkdir()
    (deck / "node_modules" / "x").write_text("x")
    (deck / "a" / "node_modules").mkdir(parents=True)
    (deck / "a" / "node_modules" / "y").write_text("y")

    with fake_worker(volume, replying(BUILT_BY_STEP)) as seen:
        built = toolchain(volume).ran(step)

    assert built is None
    assert sorted(tree_of(seen.uploads[0])) == [
        "a",
        "a/node_modules",
        "a/node_modules/y",
        "slides.md",
    ]


def a_deck_over_the_megabytes(deck: Path) -> None:
    with (deck / "big").open("wb") as big:
        big.truncate(MEGABYTES * MEGABYTE)


def a_deck_with_a_deep_path(deck: Path) -> None:
    deepest = deck
    for _ in range(33):
        deepest = deepest / "d"
        deepest.mkdir()


@pytest.mark.parametrize(
    ("make", "failure"),
    [
        pytest.param(
            a_deck_over_the_megabytes,
            BuildFailure(
                reason=BuildFailureReason.DECK_MEGABYTES,
                allowed_megabytes=MEGABYTES,
                allowed_entries=MAXIMUM_ENTRIES,
            ),
            id="megabytes",
        ),
        pytest.param(
            a_deck_with_a_deep_path,
            BuildFailure(reason=BuildFailureReason.DECK_PATHS),
            id="path rule",
        ),
    ],
)
def test_a_deck_over_a_limit_or_breaking_a_rule_says_so_and_connects_to_nothing(
    volume: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    make: Callable[[Path], None],
    failure: BuildFailure,
) -> None:
    """Cell B10: no socket exists, so a connect would have retried until W."""
    step = a_step(tmp_path)
    make(step.place.here / DECK_DIRECTORY)
    started = time.monotonic()

    with json_logged(capsys) as logged:
        result = toolchain(volume, connect_window_seconds=30.0).ran(step)
        line = ended_line(step, logged)

    assert result == failure
    assert time.monotonic() - started < PROMPTLY
    assert (line["outcome"], line["reason"], line["level"]) == (
        "refused",
        failure.reason.value,
        "info",
    )


def test_a_deck_that_cannot_be_walked_fails_without_a_reason(
    volume: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fault table: a deck-walk OSError other than the shape class."""
    step = a_step(tmp_path)

    def failing_stat(*_arguments: object, **_options: object) -> os.stat_result:
        raise OSError(errno.EIO, "input/output error")

    monkeypatch.setattr(os, "stat", failing_stat)

    assert toolchain(volume).ran(step) == WITHOUT_A_REASON


def test_a_run_without_its_deck_fails_without_a_reason(
    volume: Path, tmp_path: Path
) -> None:
    """Sentence B4: a run with its deck gone fails as a BuildFailure, never a raise."""
    step = a_step(tmp_path)
    (step.place.here / DECK_DIRECTORY / "slides.md").unlink()
    (step.place.here / DECK_DIRECTORY).rmdir()

    assert toolchain(volume).ran(step) == WITHOUT_A_REASON


def test_a_deck_changing_after_the_survey_fails_without_a_reason(
    volume: Path, tmp_path: Path
) -> None:
    """Fault table: a deck that changes between the survey and the upload."""
    step = a_step(tmp_path)

    def changing_the_deck(
        connection: socket.socket, _seen: Seen, _uploads: Path
    ) -> None:
        read_request(connection)
        (step.place.here / DECK_DIRECTORY / "link").symlink_to("slides.md")
        send_status(connection)

    with fake_worker(volume, proven(changing_the_deck)):
        result = toolchain(volume).ran(step)

    assert result == WITHOUT_A_REASON
    assert not staging_of(step).exists()


def test_a_worker_closing_while_the_deck_is_uploaded_fails_without_a_reason(
    volume: Path, tmp_path: Path
) -> None:
    """Cell B5: EPIPE or ECONNRESET after STATUS is not retried."""
    step = a_step(tmp_path)
    (step.place.here / DECK_DIRECTORY / "big").write_bytes(b"x" * (MEGABYTE // 2))

    def closing_after_status(
        connection: socket.socket, _seen: Seen, _uploads: Path
    ) -> None:
        read_request(connection)
        send_status(connection)

    with fake_worker(volume, proven(closing_after_status)) as _:
        result = toolchain(volume).ran(step)

    assert result == WITHOUT_A_REASON
    assert not staging_of(step).exists()


def test_a_worker_closing_in_the_middle_of_its_reply_fails_without_a_reason(
    volume: Path, tmp_path: Path
) -> None:
    """Cell B5 (I): the worker closes in the middle of its reply."""
    step = a_step(tmp_path)

    def closing_mid_reply(connection: socket.socket, _step: StepHeader) -> None:
        connection.sendall(support.OUTCOME_BUILT)

    with fake_worker(volume, answering(closing_mid_reply)):
        result = toolchain(volume).ran(step)

    assert result == WITHOUT_A_REASON
    assert not staging_of(step).exists()
