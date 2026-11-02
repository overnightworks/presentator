"""The service answerer runs one turn through the client and maps how it ends."""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
from typing import TYPE_CHECKING

import pytest
from agent_runtime_client.backend import Asker
from agent_runtime_client.capability import CapabilityPrincipal
from agent_runtime_client.wire import (
    PROTOCOL_VERSION,
    FailureCode,
    RejectionCode,
    RunRequest,
    TurnRole,
)

from presentator.contracts.copresenter import NotEnabledError
from tests.presentator.agent.support.agent_service_support import (
    GOOD_ANSWER,
    ISSUER,
    PERSON,
    QUESTION,
    SERVICE_MESSAGE,
    SUBJECT,
    VIOLATION_DETAIL,
    WORKLOAD,
    Outcome,
    Reply,
    Token,
    Wire,
    an_answerer,
    an_asker,
    assistant_text,
    byte_by_byte,
    cancelled,
    event_frames,
    failed,
    one_warning,
    refusal_reply,
    reply_with,
    run_started,
    succeeded,
    succeeded_with_a_file_and_no_words,
    summary_lines,
    unavailable,
    violating_frame,
    whole,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from tests.presentator.agent.support.agent_service_support import Step


_WAIT_SECONDS = 5


def test_the_request_is_the_run_request_of_the_contract() -> None:
    asker = an_asker(reply_with(*whole(event_frames(*GOOD_ANSWER))))

    asyncio.run(asker.ask())

    (request,) = asker.wire.requests
    assert (request.method, request.path) == ("POST", "/runs")
    run = RunRequest.model_validate_json(request.content)
    assert run.v == PROTOCOL_VERSION
    assert run.tools == {}
    assert run.turn.system
    assert [(m.role, m.text) for m in run.turn.messages] == [(TurnRole.USER, QUESTION)]


def test_both_tokens_go_with_every_run_and_each_is_awaited_once() -> None:
    asker = an_asker(reply_with(*whole(event_frames(*GOOD_ANSWER))))

    asyncio.run(asker.ask())

    (request,) = asker.wire.requests
    assert request.headers["authorization"] == f"Bearer {PERSON}"
    assert request.headers["agent-workload-token"] == WORKLOAD
    assert (asker.person.calls, asker.workload.calls) == (1, 1)


@pytest.mark.parametrize("split", [whole, byte_by_byte], ids=["whole", "bytes"])
def test_the_answer_is_the_assistant_text(
    split: Callable[[list[bytes]], list[bytes]],
) -> None:
    asker = an_asker(reply_with(*split(event_frames(*GOOD_ANSWER))))

    outcome = asyncio.run(asker.ask())

    assert outcome == Outcome(["Hallo ", "Welt."], None)


def _extra_in_the_terminals_chunk(frames: list[bytes]) -> list[bytes]:
    return whole(frames)


def _extra_in_a_later_chunk(frames: list[bytes]) -> list[bytes]:
    return [*whole(frames[:3]), frames[3]]


@pytest.mark.parametrize(
    "arrange",
    [_extra_in_the_terminals_chunk, _extra_in_a_later_chunk],
    ids=["same-chunk", "later-chunk"],
)
def test_the_run_ends_at_its_terminal_and_what_follows_it_is_never_read(
    arrange: Callable[[list[bytes]], list[bytes]],
) -> None:
    frames = event_frames(
        run_started(), assistant_text("Hallo"), succeeded(), assistant_text("zu viel")
    )
    asker = an_asker(reply_with(*arrange(frames)))

    outcome = asyncio.run(asker.ask())

    assert outcome == Outcome(["Hallo"], None)


def test_a_run_that_ends_with_files_and_no_words_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asker = an_asker(
        reply_with(*event_frames(run_started(), succeeded_with_a_file_and_no_words()))
    )

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == []
    unavailable(outcome)
    assert "files and no words" in one_warning(caplog)


def test_a_run_with_words_keeps_them_though_it_also_left_a_file() -> None:
    asker = an_asker(
        reply_with(
            *event_frames(
                run_started(),
                assistant_text("Hallo"),
                succeeded_with_a_file_and_no_words(),
            )
        )
    )

    assert asyncio.run(asker.ask()) == Outcome(["Hallo"], None)


@pytest.mark.parametrize(
    "code", [code for code in FailureCode if code is not FailureCode.INTERRUPTED]
)
def test_a_failed_run_ends_as_unavailable_and_names_its_code(
    code: FailureCode, caplog: pytest.LogCaptureFixture
) -> None:
    asker = an_asker(
        reply_with(*event_frames(run_started(), assistant_text("Hallo"), failed(code)))
    )

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == ["Hallo"]
    unavailable(outcome)
    assert code.value in one_warning(caplog)


@pytest.mark.parametrize(
    ("ending", "named"),
    [
        (failed(FailureCode.INTERRUPTED), "interrupted_without_side_effect"),
        (cancelled(), "cancelled"),
    ],
    ids=["interrupted", "cancelled"],
)
def test_an_interrupted_or_cancelled_run_ends_as_unavailable(
    ending: Step, named: str, caplog: pytest.LogCaptureFixture
) -> None:
    asker = an_asker(
        reply_with(*event_frames(run_started(), assistant_text("Hallo"), ending))
    )

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == ["Hallo"]
    unavailable(outcome)
    assert named in one_warning(caplog)


def test_a_stream_without_a_terminal_event_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asker = an_asker(reply_with(*event_frames(run_started(), assistant_text("Hallo"))))

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == ["Hallo"]
    unavailable(outcome)
    assert "truncated" in one_warning(caplog)


def test_a_sequence_gap_is_unavailable_and_nothing_after_it_is_yielded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    frames = event_frames(
        run_started(),
        assistant_text("Hallo"),
        assistant_text("fehlt"),
        assistant_text("danach"),
    )
    asker = an_asker(reply_with(*whole([frames[0], frames[1], frames[3]])))

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == ["Hallo"]
    unavailable(outcome)
    assert "sequence_gap" in one_warning(caplog)


_SERVICE_STATUS = {
    RejectionCode.NOT_ENABLED: 403,
    RejectionCode.FORBIDDEN: 403,
    RejectionCode.BUSY: 429,
    RejectionCode.UNAUTHORIZED: 401,
    RejectionCode.UNAVAILABLE: 503,
    RejectionCode.UNSUPPORTED_VERSION: 400,
    RejectionCode.INVALID_REQUEST: 422,
    RejectionCode.CAPABILITY_TOO_LARGE: 422,
    RejectionCode.NOT_FOUND: 404,
    RejectionCode.TOO_LARGE: 413,
    RejectionCode.MODEL_NOT_OFFERED: 422,
}
_SERVICE_RETRY_AFTER = {RejectionCode.BUSY: "10"}


@pytest.mark.parametrize("code", list(RejectionCode))
def test_every_rejection_is_unavailable_and_logs_its_code(
    code: RejectionCode, caplog: pytest.LogCaptureFixture
) -> None:
    retry_after = _SERVICE_RETRY_AFTER.get(code)
    headers = {} if retry_after is None else {"retry-after": retry_after}
    asker = an_asker(refusal_reply(code, _SERVICE_STATUS[code], **headers))

    outcome = asyncio.run(asker.ask())

    unavailable(outcome)
    warning = one_warning(caplog)
    assert code.value in warning
    assert (retry_after or "None") in warning


_UNKNOWN_CODE = (
    b'{"v": %d, "code": "bribe_me", "message": "Dienst sagt nein"}' % PROTOCOL_VERSION
)


@pytest.mark.parametrize(
    "reply",
    [
        Reply(
            status=403,
            headers={"content-type": "application/json"},
            chunks=[_UNKNOWN_CODE],
        ),
        Reply(
            status=502, headers={"content-type": "text/html"}, chunks=[b"<html></html>"]
        ),
        Reply(
            status=200,
            headers={"content-type": "application/json"},
            chunks=[b"{}"],
        ),
        refusal_reply(RejectionCode.BUSY, 200),
        Reply(status=307, headers={"location": "http://elsewhere.test/runs"}),
    ],
    ids=["unknown-code", "html-502", "json-200", "rejection-looking-200", "redirect"],
)
def test_a_refusal_without_a_known_code_is_unavailable(
    reply: Reply, caplog: pytest.LogCaptureFixture
) -> None:
    asker = an_asker(reply)

    outcome = asyncio.run(asker.ask())

    unavailable(outcome)
    warning = one_warning(caplog)
    assert f"refused {RejectionCode.UNAVAILABLE.value}" in warning
    assert "bribe_me" not in warning
    assert len(asker.wire.requests) == 1


@pytest.mark.parametrize("retry_after", ["soon", "-5", "1e3", "٣"])
def test_a_retry_after_that_is_not_a_whole_number_of_seconds_is_not_logged(
    retry_after: str, caplog: pytest.LogCaptureFixture
) -> None:
    asker = an_asker(
        refusal_reply(RejectionCode.BUSY, 429, **{"retry-after": retry_after})
    )

    asyncio.run(asker.ask())

    warning = one_warning(caplog)
    assert "retry_after=None" in warning
    assert retry_after not in warning


def test_a_service_that_cannot_be_reached_is_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asker = an_asker(Reply(refuses_connection=True))

    outcome = asyncio.run(asker.ask())

    unavailable(outcome)
    assert "transport_lost" in one_warning(caplog)


def test_a_transport_failure_mid_stream_is_unavailable_after_the_text() -> None:
    asker = an_asker(
        Reply(
            chunks=whole(event_frames(run_started(), assistant_text("Hallo"))),
            breaks_after_chunks=True,
        )
    )

    outcome = asyncio.run(asker.ask())

    assert outcome.texts == ["Hallo"]
    unavailable(outcome)


def test_leaving_early_closes_the_response() -> None:
    asker = an_asker(
        Reply(
            chunks=whole(event_frames(run_started(), assistant_text("Hallo"))),
            hangs_after_chunks=True,
        )
    )

    async def leave_after_first_text() -> None:
        with asker.wire:
            async with asker.answerer().answer(
                message=QUESTION, subject=SUBJECT, person_token=asker.person
            ) as stream:
                assert await anext(stream) == "Hallo"
            assert await asyncio.to_thread(asker.wire.closed.wait, _WAIT_SECONDS)

    asyncio.run(leave_after_first_text())


def test_one_asks_end_leaves_another_intact() -> None:
    first_left = threading.Event()
    second_frames = event_frames(
        run_started(),
        assistant_text("zweite "),
        assistant_text("Antwort."),
        succeeded(),
    )

    def reply_for(question: str) -> Reply:
        if question == "first":
            return Reply(
                chunks=whole(event_frames(run_started(), assistant_text("erst"))),
                hangs_after_chunks=True,
            )
        return Reply(
            chunks=whole(second_frames[:2]),
            gate=first_left,
            late_chunks=whole(second_frames[2:]),
        )

    async def drive() -> list[str]:
        with Wire(reply_for) as wire:
            answerer = an_answerer(wire.origin, Token(WORKLOAD))
            person = Token(PERSON)
            async with answerer.answer(
                message="second", subject=SUBJECT, person_token=person
            ) as second:
                async with answerer.answer(
                    message="first", subject=SUBJECT, person_token=person
                ) as first:
                    assert await anext(first) == "erst"
                first_left.set()
                return [text async for text in second]

    assert asyncio.run(drive()) == ["zweite ", "Antwort."]


@pytest.mark.parametrize("suffix", ["", "/"], ids=["bare", "trailing-slash"])
def test_the_run_is_posted_to_the_origins_runs_path(suffix: str) -> None:
    """#452: the origin with or without a trailing slash posts to /runs."""

    async def ask(wire: Wire) -> None:
        answerer = an_answerer(wire.origin + suffix, Token(WORKLOAD))
        async with answerer.answer(
            message=QUESTION, subject=SUBJECT, person_token=Token(PERSON)
        ) as texts:
            assert [text async for text in texts]

    with Wire(reply_with(*whole(event_frames(*GOOD_ANSWER)))) as wire:
        asyncio.run(ask(wire))

    (request,) = wire.requests
    assert request.path == "/runs"


@pytest.mark.parametrize("code", list(RejectionCode))
def test_only_a_not_enabled_refusal_is_a_not_enabled_error(code: RejectionCode) -> None:
    """#452: only not_enabled raises NotEnabledError."""
    asker = an_asker(refusal_reply(code, 403))

    error = unavailable(asyncio.run(asker.ask()))

    assert isinstance(error, NotEnabledError) is (code is RejectionCode.NOT_ENABLED)


def _ticking_clock() -> Callable[[], float]:
    ticks = itertools.count()
    return lambda: float(next(ticks))


def _summary_of_a_run(reply: Reply, caplog: pytest.LogCaptureFixture) -> str:
    caplog.set_level(logging.INFO)
    asker = an_asker(reply, monotonic=_ticking_clock())
    asyncio.run(asker.ask())
    (summary,) = summary_lines(caplog)
    return summary


def test_a_good_run_logs_its_event_types_and_times_counted_from_the_post(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#452: the run summary of a good run."""
    summary = _summary_of_a_run(reply_with(*whole(event_frames(*GOOD_ANSWER))), caplog)

    assert summary == (
        "agent service run summary: events=AssistantTextEvent:2,FinalEvent:1 "
        "stop=None first_text_ms=1000 end_ms=2000"
    )


def test_a_failed_run_logs_one_summary_without_the_service_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#452: the run summary of a failed run."""
    reply = reply_with(
        *event_frames(
            run_started(), assistant_text("Hallo"), failed(FailureCode.PROVIDER_ERROR)
        )
    )

    summary = _summary_of_a_run(reply, caplog)

    assert "events=AssistantTextEvent:1 stop=failed" in summary
    assert SERVICE_MESSAGE not in summary


def test_a_stream_violation_logs_its_kind_and_not_its_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#452: the run summary of a violation."""
    frames = event_frames(run_started())

    summary = _summary_of_a_run(reply_with(*frames, violating_frame()), caplog)

    assert "events= stop=unsupported_version" in summary
    assert "first_text_ms=None" in summary
    assert VIOLATION_DETAIL not in summary


def test_leaving_before_the_first_read_opens_no_run_and_logs_no_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#452: the run summary of an early leave."""
    caplog.set_level(logging.INFO)
    asker = an_asker(reply_with(*whole(event_frames(*GOOD_ANSWER))))

    async def leave() -> None:
        with asker.wire:
            async with asker.answerer().answer(
                message=QUESTION, subject=SUBJECT, person_token=asker.person
            ):
                pass

    asyncio.run(leave())

    assert asker.wire.requests == []
    assert summary_lines(caplog) == []


def test_a_refusal_before_the_stream_logs_no_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#452: a refusal opens no run, so no summary."""
    caplog.set_level(logging.INFO)
    asker = an_asker(refusal_reply(RejectionCode.BUSY, 429))

    asyncio.run(asker.ask())

    assert summary_lines(caplog) == []


def test_the_run_names_the_asking_person_with_the_issuer_and_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#709 M3b: the run's principal is the verified person, never none."""
    principals: list[CapabilityPrincipal | None] = []

    class RecordingBackend:
        def __init__(self, _config: object) -> None:
            pass

        async def run(self, _request: object, asker: Asker) -> AsyncGenerator[None]:
            principals.append(asker.principal)
            return
            yield

    monkeypatch.setattr("presentator.agent.backend.ServiceBackend", RecordingBackend)

    async def ask() -> None:
        answerer = an_answerer("http://service.invalid", Token(WORKLOAD))
        async with answerer.answer(
            message=QUESTION, subject=SUBJECT, person_token=Token(PERSON)
        ) as texts:
            assert [text async for text in texts] == []

    asyncio.run(ask())

    assert principals == [CapabilityPrincipal(iss=ISSUER, sub=SUBJECT)]
