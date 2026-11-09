"""The answerer over the gateway: the request it builds and how every stop ends."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

import httpx2
import pytest
from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import TurnStopKind, TurnStopped
from agent_runtime_client.backend.fake_gateway import (
    FakeGateway,
    Reply,
    completion,
    tool_call,
    tool_round,
)
from agent_runtime_client.turn import (
    FailureCode,
    Principal,
    RejectionCode,
    person_fingerprint,
)
from agent_runtime_client.turn_events import (
    AssistantTextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

from presentator.agent.backend import (
    COPRESENTER_AGENT_FILE,
    SHOW_SLIDE,
    GatewayAnswerer,
    gateway_answerer,
)
from presentator.contracts.copresenter import (
    AskingPerson,
    CoPresenterUnavailableError,
    NotEnabledError,
    ShowSlide,
)
from presentator.contracts.models import Role
from tests.presentator.agent.support.driving import on_an_event_loop
from tests.presentator.agent.support.transports import RefusingTransport
from tests.presentator.application.support.fakes import FrozenClock
from tests.support.leak_support import everything_shown
from tests.support.lobby import (
    A_GATEWAY_KEY,
    A_GATEWAY_KEY_FILE,
    AN_API_GROUP,
    AN_ISSUER,
    NOW,
    a_relying_party,
    an_agent_config,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agent_runtime_client.agents import TurnRequest
    from agent_runtime_client.backend import TurnEvent, TurnPerson
    from agent_runtime_client.tools import JsonObject

LOGGER = "presentator.agent.backend"
# The fixed vector of agent-runtime's docs/wire.md for this issuer and subject.
WIRE_VECTOR = "fp-2db44d96ffdef38f9e1cbfe73e9cf248fb66955b894846b34843a2d3a849d764"
AN_ISSUER_OF_THE_VECTOR = "https://issuer.example"
THE_QUESTION = "Was steht auf Folie zwei?"


def a_person(*, groups: frozenset[str] = frozenset({AN_API_GROUP})) -> AskingPerson:
    return AskingPerson(subject="subject-1", role=Role.ADMIN, groups=groups)


def answerer_over(
    transport: httpx2.AsyncBaseTransport, *, issuer: str = AN_ISSUER
) -> GatewayAnswerer:
    relying_party = a_relying_party(FrozenClock(instant=NOW), issuer=issuer)
    return gateway_answerer(an_agent_config(), relying_party, transport=transport)


async def answered(
    answerer: GatewayAnswerer, person: AskingPerson | None = None
) -> list[str | ShowSlide]:
    async with answerer.answer(
        message=THE_QUESTION, person=person or a_person()
    ) as events:
        return [event async for event in events]


async def spoken_to(
    answerer: GatewayAnswerer, message: str, person: AskingPerson | None = None
) -> str:
    async with answerer.answer(message=message, person=person or a_person()) as texts:
        return "".join([text async for text in texts if isinstance(text, str)])


async def spoken(answerer: GatewayAnswerer, person: AskingPerson | None = None) -> str:
    return await spoken_to(answerer, THE_QUESTION, person)


def body_of(gateway: FakeGateway) -> dict[str, object]:
    (request,) = gateway.requests
    return json.loads(request.content)


@on_an_event_loop
async def test_the_request_names_the_agent_files_model_the_words_and_the_key() -> None:
    """CP1, CP5: model, bearer, messages and the one tool, with no issuer or subject."""
    gateway = FakeGateway(completion("Eine Antwort."))

    answered = await spoken(answerer_over(gateway))

    (request,) = gateway.requests
    body = body_of(gateway)
    agent = load_agent(COPRESENTER_AGENT_FILE, [SHOW_SLIDE.declaration])
    assert answered == "Eine Antwort."
    assert body["model"] == agent.model
    assert [tool["function"]["name"] for tool in body["tools"]] == ["show_slide"]
    assert THE_QUESTION in json.dumps(body["messages"], ensure_ascii=False)
    assert request.headers["authorization"] == (
        f"Bearer {A_GATEWAY_KEY_FILE.read_text(encoding='utf-8').strip()}"
    )
    assert body["user"] == person_fingerprint(Principal(iss=AN_ISSUER, sub="subject-1"))
    for identity in (AN_ISSUER, "subject-1"):
        assert identity not in json.dumps(body, ensure_ascii=False)


@on_an_event_loop
async def test_the_person_goes_out_as_the_wire_vector_with_the_issuer_as_held() -> None:
    """The vector of docs/wire.md, and another one for a trailing slash."""
    plain = FakeGateway(completion("Ja."))
    slashed = FakeGateway(completion("Ja."))

    await spoken(answerer_over(plain, issuer=AN_ISSUER_OF_THE_VECTOR))
    await spoken(answerer_over(slashed, issuer=f"{AN_ISSUER_OF_THE_VECTOR}/"))

    assert body_of(plain)["user"] == WIRE_VECTOR
    assert body_of(slashed)["user"] != WIRE_VECTOR


@on_an_event_loop
async def test_a_person_outside_the_api_group_is_not_enabled_and_no_call_is_made() -> (
    None
):
    """CP3: the client admits only a member of the API group."""
    gateway = FakeGateway(completion("Nie."))

    answerer = answerer_over(gateway)
    outsider = a_person(groups=frozenset({"/other"}))

    with pytest.raises(NotEnabledError):
        await spoken(answerer, outsider)

    assert gateway.requests == []


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(Reply(429, {"retry-after": "7"}), id="429-with-a-wait"),
        pytest.param(Reply(429), id="429-without-a-wait"),
        pytest.param(Reply(401), id="401"),
        pytest.param(Reply(403), id="403"),
        pytest.param(Reply(422), id="422-budget"),
        pytest.param(Reply(500), id="500"),
        pytest.param(Reply(200, body=b"not json"), id="malformed-reply"),
        pytest.param(completion(""), id="no-words"),
    ],
)
@on_an_event_loop
async def test_every_gateway_refusal_ends_as_the_plain_unavailable_error(
    reply: Reply,
) -> None:
    """No reason reaches the overlay, whatever the gateway said."""
    answerer = answerer_over(FakeGateway(reply))

    with pytest.raises(CoPresenterUnavailableError) as stopped:
        await spoken(answerer)

    assert type(stopped.value) is CoPresenterUnavailableError
    assert (stopped.value.__cause__, stopped.value.__context__) == (None, None)


@on_an_event_loop
async def test_a_refused_connection_ends_as_the_plain_unavailable_error() -> None:
    """The refusing transport."""
    refusing = RefusingTransport()

    answerer = answerer_over(refusing)

    with pytest.raises(CoPresenterUnavailableError):
        await spoken(answerer)

    assert len(refusing.requests) == 1


class EchoingTransport(httpx2.MockTransport):
    """Fails every request with an error text that repeats these words."""

    def __init__(self, words: str) -> None:
        """Remember `words` for the error text."""
        super().__init__(self._fail)
        self._words = words

    async def _fail(self, request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(self._words, request=request)


@pytest.mark.parametrize("through", ["reply", "transport-error"])
@on_an_event_loop
async def test_what_the_gateway_echoes_reaches_no_exception_text_or_log_record(
    through: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The key, token, subject, groups and question stay out of errors."""
    caplog.set_level(logging.DEBUG)
    persons_bearer_marker = "person-token-MARKER"
    person = AskingPerson(
        subject="subject-MARKER",
        role=Role.ADMIN,
        groups=frozenset({AN_API_GROUP, "/group-MARKER"}),
    )
    echoed = (
        f"{A_GATEWAY_KEY} {persons_bearer_marker} {person.subject} /group-MARKER "
        f"{THE_QUESTION}"
    )
    transport: httpx2.AsyncBaseTransport = (
        FakeGateway(Reply(500, body=echoed.encode()))
        if through == "reply"
        else EchoingTransport(echoed)
    )

    answerer = answerer_over(transport)

    with pytest.raises(CoPresenterUnavailableError) as stopped:
        await spoken(answerer, person)

    shown = everything_shown(caplog.records, errors=[stopped.value])
    for marker in (
        A_GATEWAY_KEY,
        persons_bearer_marker,
        person.subject,
        "/group-MARKER",
        THE_QUESTION,
    ):
        assert marker not in shown


@on_an_event_loop
async def test_a_request_over_the_clients_bound_ends_unavailable() -> None:
    """The client sends nothing it judges too large."""
    gateway = FakeGateway(completion("Nie."))

    answering = answerer_over(gateway).answer(
        message="x" * 5_000_000, person=a_person()
    )

    with pytest.raises(CoPresenterUnavailableError):
        async with answering as texts:
            _ = [text async for text in texts]

    assert gateway.requests == []


@on_an_event_loop
async def test_a_question_that_is_not_valid_unicode_ends_unavailable() -> None:
    """The request is refused before it is built."""
    gateway = FakeGateway(completion("Nie."))

    answering = answerer_over(gateway).answer(message="\ud800", person=a_person())

    with pytest.raises(CoPresenterUnavailableError):
        async with answering as texts:
            _ = [text async for text in texts]

    assert gateway.requests == []


class ScriptedBackend:
    """A backend that streams these events and then ends the turn with its stop."""

    def __init__(
        self, events: tuple[TurnEvent, ...] = (), stop: TurnStopped | None = None
    ) -> None:
        """Stream `events`, then raise `stop` unless there is none."""
        self._events = events
        self._stop = stop

    async def run(
        self, request: TurnRequest, person: TurnPerson
    ) -> AsyncGenerator[TurnEvent]:
        del request, person
        for event in self._events:
            yield event
        if self._stop is not None:
            raise self._stop


ALL_STOPS = [
    *((TurnStopKind.REFUSED, code) for code in RejectionCode),
    *((TurnStopKind.FAILED, code) for code in FailureCode),
]


@pytest.mark.parametrize(("kind", "code"), ALL_STOPS)
@on_an_event_loop
async def test_only_a_not_enabled_refusal_is_not_enabled(
    kind: TurnStopKind, code: RejectionCode | FailureCode
) -> None:
    """Every kind and code of a stop has a mapping."""
    answerer = GatewayAnswerer(
        ScriptedBackend(stop=TurnStopped(kind, code, retry_after_s=7, detail="closed")),
        a_relying_party(FrozenClock(instant=NOW)),
    )
    expected = (
        NotEnabledError
        if (kind, code) == (TurnStopKind.REFUSED, RejectionCode.NOT_ENABLED)
        else CoPresenterUnavailableError
    )

    with pytest.raises(CoPresenterUnavailableError) as stopped:
        await spoken(answerer)

    assert type(stopped.value) is expected


@on_an_event_loop
async def test_a_refusal_logs_its_closed_values_and_no_run_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Kind, code, detail and the wait, and nothing else."""
    caplog.set_level(logging.DEBUG)

    answerer = answerer_over(FakeGateway(Reply(429, {"retry-after": "7"})))

    with pytest.raises(CoPresenterUnavailableError):
        await spoken(answerer)

    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert "refused" in line
    assert "busy" in line
    assert "retry_after_s=7" in line
    assert "summary" not in line


@on_an_event_loop
async def test_a_run_logs_its_event_types_and_times_and_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The summary names what happened, never what was said."""
    caplog.set_level(logging.DEBUG)

    await spoken(answerer_over(FakeGateway(completion("Geheime Worte."))))

    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert "AssistantTextEvent:1" in line
    assert "FinalEvent:1" in line
    assert "Geheime" not in line
    assert THE_QUESTION not in line


@on_an_event_loop
async def test_a_stream_that_ends_without_a_final_event_ends_the_answer() -> None:
    """What was streamed is the answer; the stream's end ends it."""
    answerer = GatewayAnswerer(
        ScriptedBackend((AssistantTextEvent(text="Hallo."),)),
        a_relying_party(FrozenClock(instant=NOW)),
    )

    assert await spoken(answerer) == "Hallo."


@on_an_event_loop
async def test_a_tool_step_in_the_stream_is_not_spoken() -> None:
    """Only the assistant's words are the answer."""
    answerer = GatewayAnswerer(
        ScriptedBackend(
            (
                ToolCallEvent(tool_use_id="t", name="look"),
                ToolResultEvent(tool_use_id="t", content="secret"),
                AssistantTextEvent(text="Hallo."),
            )
        ),
        a_relying_party(FrozenClock(instant=NOW)),
    )

    assert await answered(answerer) == ["Hallo."]


@on_an_event_loop
async def test_one_asks_end_leaves_another_intact() -> None:
    """Closing one ask cancels only its own call, not an overlapping ask's."""
    first_left = asyncio.Event()
    first_arrived = asyncio.Event()
    first_cancelled = False

    async def reply_for(request: httpx2.Request) -> httpx2.Response:
        nonlocal first_cancelled
        if b"first-question" in request.content:
            first_arrived.set()
            try:
                await asyncio.get_running_loop().create_future()
            except asyncio.CancelledError:
                first_cancelled = True
                raise
        await first_left.wait()
        return httpx2.Response(200, content=completion("Zweite Antwort.").body)

    answerer = gateway_answerer(
        an_agent_config(),
        a_relying_party(FrozenClock(instant=NOW)),
        transport=httpx2.MockTransport(reply_for),
    )

    async def ask(message: str) -> str:
        return await spoken_to(answerer, message)

    second = asyncio.create_task(ask("second-question"))
    first = asyncio.create_task(ask("first-question"))
    await first_arrived.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    first_left.set()

    assert await second == "Zweite Antwort."
    assert first_cancelled


def a_slide_round(arguments: JsonObject) -> list[Reply]:
    return [
        tool_round(tool_call("call-1", "show_slide", arguments)),
        completion("Gut."),
    ]


@on_an_event_loop
async def test_a_slide_move_comes_before_the_words_that_follow_it() -> None:
    """CP1, CP6: a show_slide round followed by text yields the move, then the text."""
    gateway = FakeGateway(*a_slide_round({"number": 3}))

    events = await answered(answerer_over(gateway))

    assert events == [ShowSlide(number=3), "Gut."]


@pytest.mark.parametrize(
    "arguments", [{"number": 0}, {"number": -2}, {"number": 1.5}, {"number": True}, {}]
)
@on_an_event_loop
async def test_a_bad_slide_number_is_an_error_result_and_moves_nothing(
    arguments: JsonObject,
) -> None:
    """The model is told on round two; the window is not."""
    gateway = FakeGateway(*a_slide_round(arguments))

    events = await answered(answerer_over(gateway))

    second = json.dumps(json.loads(gateway.requests[1].content)["messages"])
    assert events == ["Gut."]
    assert "at least 1" in second


@on_an_event_loop
async def test_the_run_summary_logs_the_time_to_the_first_move(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The move time is measured beside the run's end."""
    caplog.set_level(logging.DEBUG)

    await answered(answerer_over(FakeGateway(*a_slide_round({"number": 2}))))

    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert " move_ms=" in line
