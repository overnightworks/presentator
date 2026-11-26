"""The scenarios that look for a marker leaking out of a failing speech hop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from agents.backend.fake_gateway import FakeGateway, completion
from speech_client.relay import HopClose

from tests.support.copresenter_support import (
    Gateway,
    the_refresh_token_is_revoked,
    the_session_ends_at_the_reopening,
    the_session_ends_during_the_grant,
    the_token_thread_fails,
)
from tests.support.hearing_endings import HEARING_ENDS
from tests.support.speech_drive import (
    ANSWER_SENTENCES,
    HEARD_WORDS,
    SpeechScenario,
)
from tests.support.speech_service import (
    HEALTH_FAILURES,
    MALFORMED_PEERS,
    PLANTED,
    SPEAK_FAILURES,
    Answer,
    FakeSpeechService,
    Hear,
    RawPeer,
    Served,
    a_completed,
    a_delta,
    a_marked_error_event,
    a_marked_failed_item,
    after_the_first_audio,
    busy_at_the_upgrade,
    denying,
    draining,
    nobody_listens,
    one_after_another,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.support.talk_support import Hosts


def the_marked_answer() -> Gateway:
    return FakeGateway(completion(" ".join(ANSWER_SENTENCES)))


def refusing_every_workload_grant(error: str) -> Callable[[Hosts], None]:
    def arrange(hosts: Hosts) -> None:
        hosts.issuer.workload_refusal = (error, PLANTED)

    return arrange


def _hearing_the_words() -> Hear:
    first, second = HEARD_WORDS
    return after_the_first_audio(
        a_delta(first), a_delta(second), a_completed(f"{first} {second}")
    )


def _with_service(
    service: Callable[[], FakeSpeechService],
) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, service=service())


def _with_speak_answer(answer: Answer) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(speak_answers=[answer]))


def _with_realtime(realtime: Callable[[], Hear]) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(realtime=realtime()))


def _with_health_answer(answer: Answer) -> Callable[[], SpeechScenario]:
    return _with_service(lambda: FakeSpeechService(health_answer=answer))


def _with_peer(peer: Served) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, peer=peer)


def _arranged(arrange: Callable[[Hosts], None]) -> Callable[[], SpeechScenario]:
    return lambda: SpeechScenario(gateway=the_marked_answer, arrange=arrange)


def _stalled() -> SpeechScenario:
    return SpeechScenario(gateway=the_marked_answer, bounded_calls_stall=True)


def _malformed_peer_cases() -> dict[str, Callable[[], SpeechScenario]]:
    return {
        f"peer-{name}": _with_peer(RawPeer(answer).serving)
        for name, answer in MALFORMED_PEERS.items()
    }


def _refused_before_any_service_is_asked() -> dict[str, Callable[[], SpeechScenario]]:
    return {
        "marked-issuer-refusal": _arranged(refusing_every_workload_grant(PLANTED)),
        "workload-scope-refused": _arranged(
            refusing_every_workload_grant("invalid_scope")
        ),
        "thread-raises-the-token": _arranged(the_token_thread_fails),
        "revoked-refresh-token": _arranged(the_refresh_token_is_revoked),
    }


_SESSION_ENDS_DURING_THE_GRANT: Final = "session-ends-during-the-grant"


LEAK_ASKS: Final[dict[str, Callable[[], SpeechScenario]]] = {
    "good": lambda: SpeechScenario(gateway=the_marked_answer),
    **{
        f"speak-{name}": _with_speak_answer(answer)
        for name, answer in SPEAK_FAILURES.items()
    },
    **_malformed_peer_cases(),
    "nobody-listens": _with_peer(nobody_listens),
    "stalled": _stalled,
    "unspeakable-remainder": lambda: SpeechScenario(
        gateway=lambda: FakeGateway(completion(f"{ANSWER_SENTENCES[0]} …"))
    ),
    **_refused_before_any_service_is_asked(),
    _SESSION_ENDS_DURING_THE_GRANT: _arranged(the_session_ends_during_the_grant),
}


@dataclass(frozen=True)
class LeakHearing:
    """A hearing that might leak a marker, and the close it must reach."""

    scenario: Callable[[], SpeechScenario]
    close: HopClose


def _failing(
    cases: dict[str, Callable[[], SpeechScenario]],
) -> dict[str, LeakHearing]:
    return {
        name: LeakHearing(scenario, HopClose.SERVICE_FAILED)
        for name, scenario in cases.items()
    }


LEAK_HEARINGS: Final[dict[str, LeakHearing]] = {
    "good": LeakHearing(_with_realtime(_hearing_the_words), HopClose.NORMAL),
    **_failing(
        {
            "marked-error-event": _with_realtime(
                lambda: after_the_first_audio(a_marked_error_event())
            ),
            "marked-failed-then-error": _with_realtime(
                lambda: after_the_first_audio(
                    a_marked_failed_item(), a_marked_error_event()
                )
            ),
        }
    ),
    **{
        name: LeakHearing(_with_realtime(end.hear), end.close)
        for name, end in HEARING_ENDS.items()
    },
    "busy-marked": LeakHearing(
        _with_realtime(lambda: busy_at_the_upgrade(marked=True)), HopClose.BUSY
    ),
    **_failing(
        {
            f"denied-{status}": _with_realtime(
                lambda status=status: denying(status, marked=True)
            )
            for status in (401, 403, 503)
        }
    ),
    **_failing(_malformed_peer_cases()),
    **_failing({"nobody-listens": _with_peer(nobody_listens)}),
    **_failing(_refused_before_any_service_is_asked()),
    _SESSION_ENDS_DURING_THE_GRANT: LeakHearing(
        _arranged(the_session_ends_during_the_grant), HopClose.NORMAL
    ),
    "session-ends-at-the-reopening": LeakHearing(
        lambda: SpeechScenario(
            gateway=the_marked_answer,
            service=FakeSpeechService(
                realtime=one_after_another(draining(), _hearing_the_words())
            ),
            arrange=the_session_ends_at_the_reopening,
        ),
        HopClose.NORMAL,
    ),
}

LEAK_HEALTH: Final[dict[str, Callable[[], SpeechScenario]]] = {
    **{
        f"health-{name}": _with_health_answer(answer)
        for name, answer in HEALTH_FAILURES.items()
    },
    **_malformed_peer_cases(),
    "nobody-listens": _with_peer(nobody_listens),
    "stalled": _stalled,
}
