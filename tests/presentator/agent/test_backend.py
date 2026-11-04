"""The answerer over the gateway: the request it builds and how every stop ends."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx2
import pytest
from agent_runtime_client.agents import load_agent
from agent_runtime_client.backend import TurnStopKind, TurnStopped
from agent_runtime_client.backend.fake_gateway import FakeGateway, Reply, completion
from agent_runtime_client.turn import (
    FailureCode,
    Principal,
    RejectionCode,
    person_fingerprint,
)

from presentator.agent.backend import (
    COPRESENTER_AGENT_FILE,
    GatewayAnswerer,
    gateway_answerer,
)
from presentator.contracts.copresenter import (
    AskingPerson,
    CoPresenterUnavailableError,
    NotEnabledError,
)
from presentator.contracts.models import Role
from tests.presentator.agent.support.driving import on_an_event_loop
from tests.presentator.agent.support.transports import RefusingTransport
from tests.presentator.application.support.fakes import FrozenClock
from tests.support.lobby import (
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


async def spoken(answerer: GatewayAnswerer, person: AskingPerson | None = None) -> str:
    async with answerer.answer(
        message=THE_QUESTION, person=person or a_person()
    ) as texts:
        return "".join([text async for text in texts])


def body_of(gateway: FakeGateway) -> dict[str, object]:
    (request,) = gateway.requests
    return json.loads(request.content)


@on_an_event_loop
async def test_the_request_names_the_agent_files_model_the_words_and_the_key() -> None:
    """C2: model, bearer and messages, with no tools and no issuer or subject."""
    gateway = FakeGateway(completion("Eine Antwort."))

    answered = await spoken(answerer_over(gateway))

    (request,) = gateway.requests
    body = body_of(gateway)
    agent = load_agent(COPRESENTER_AGENT_FILE, [])
    assert answered == "Eine Antwort."
    assert body["model"] == agent.model
    assert "tools" not in body
    assert THE_QUESTION in json.dumps(body["messages"], ensure_ascii=False)
    assert request.headers["authorization"] == (
        f"Bearer {A_GATEWAY_KEY_FILE.read_text(encoding='utf-8').strip()}"
    )
    assert body["user"] == person_fingerprint(Principal(iss=AN_ISSUER, sub="subject-1"))
    for identity in (AN_ISSUER, "subject-1"):
        assert identity not in json.dumps(body, ensure_ascii=False)


@on_an_event_loop
async def test_the_person_goes_out_as_the_wire_vector_and_the_issuer_exactly_as_held() -> (
    None
):
    """C3: the fixed vector of docs/wire.md, and another fingerprint for a trailing slash."""
    plain = FakeGateway(completion("Ja."))
    slashed = FakeGateway(completion("Ja."))

    await spoken(answerer_over(plain, issuer=AN_ISSUER_OF_THE_VECTOR))
    await spoken(answerer_over(slashed, issuer=f"{AN_ISSUER_OF_THE_VECTOR}/"))

    assert body_of(plain)["user"] == WIRE_VECTOR
    assert body_of(slashed)["user"] != WIRE_VECTOR


@on_an_event_loop
async def test_a_person_outside_the_api_group_is_not_enabled_and_the_gateway_is_not_called() -> (
    None
):
    """C4: the client admits only a member of the API group."""
    gateway = FakeGateway(completion("Nie."))

    with pytest.raises(NotEnabledError):
        await spoken(answerer_over(gateway), a_person(groups=frozenset({"/other"})))

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
    """C7: no reason reaches the overlay, whatever the gateway said."""
    with pytest.raises(CoPresenterUnavailableError) as stopped:
        await spoken(answerer_over(FakeGateway(reply)))

    assert type(stopped.value) is CoPresenterUnavailableError
    assert (stopped.value.__cause__, stopped.value.__context__) == (None, None)


@on_an_event_loop
async def test_a_refused_connection_ends_as_the_plain_unavailable_error() -> None:
    """C7: the refusing transport."""
    refusing = RefusingTransport()

    with pytest.raises(CoPresenterUnavailableError):
        await spoken(answerer_over(refusing))

    assert len(refusing.requests) == 1


@on_an_event_loop
async def test_a_request_over_the_clients_bound_ends_as_the_plain_unavailable_error() -> (
    None
):
    """C7: the client sends nothing it judges too large."""
    gateway = FakeGateway(completion("Nie."))

    with pytest.raises(CoPresenterUnavailableError):
        async with answerer_over(gateway).answer(
            message="x" * 5_000_000, person=a_person()
        ) as texts:
            _ = [text async for text in texts]

    assert gateway.requests == []


@on_an_event_loop
async def test_a_question_that_is_not_valid_unicode_ends_as_the_plain_unavailable_error() -> (
    None
):
    """C7: the request is refused before it is built."""
    gateway = FakeGateway(completion("Nie."))

    with pytest.raises(CoPresenterUnavailableError):
        async with answerer_over(gateway).answer(
            message="\ud800", person=a_person()
        ) as texts:
            _ = [text async for text in texts]

    assert gateway.requests == []


class StoppingBackend:
    """A backend whose turn ends short in exactly one way."""

    def __init__(self, stop: TurnStopped) -> None:
        self._stop = stop

    async def run(
        self, request: TurnRequest, person: TurnPerson
    ) -> AsyncGenerator[TurnEvent]:
        del request, person
        raise self._stop
        yield


ALL_STOPS = [
    *((TurnStopKind.REFUSED, code) for code in RejectionCode),
    *((TurnStopKind.FAILED, code) for code in FailureCode),
    (TurnStopKind.CANCELLED, None),
]


@pytest.mark.parametrize(("kind", "code"), ALL_STOPS)
@on_an_event_loop
async def test_only_a_not_enabled_refusal_is_not_enabled(
    kind: TurnStopKind, code: RejectionCode | FailureCode | None
) -> None:
    """C7, C8: every kind and code of a stop has a mapping."""
    answerer = GatewayAnswerer(
        StoppingBackend(TurnStopped(kind, code, retry_after_s=7, detail="closed")),
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
    """C7: kind, code, detail and the wait, and nothing else."""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(CoPresenterUnavailableError):
        await spoken(answerer_over(FakeGateway(Reply(429, {"retry-after": "7"}))))

    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert "refused" in line
    assert "busy" in line
    assert "retry_after_s=7" in line
    assert "summary" not in line


@on_an_event_loop
async def test_a_run_logs_its_event_types_and_times_and_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """C7: the summary names what happened, never what was said."""
    caplog.set_level(logging.DEBUG)

    await spoken(answerer_over(FakeGateway(completion("Geheime Worte."))))

    (line,) = [r.getMessage() for r in caplog.records if r.name == LOGGER]
    assert "AssistantTextEvent:1" in line
    assert "FinalEvent:1" in line
    assert "Geheime" not in line
    assert THE_QUESTION not in line
