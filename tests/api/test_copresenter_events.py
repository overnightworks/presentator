"""Every generated answer event of the co-presenter says it is AI generated."""

from __future__ import annotations

import json
import re

from presentator.contracts.copresenter import CoPresenterUnavailableError
from tests.api.copresenter_support import a_copresenter_talk_host, ask


def test_every_generated_answer_event_says_ai_generated_and_error_does_not() -> None:
    """#293 E9 (a): text, sentence, audio and done say ai_generated; error does not."""
    lobby, private, _clock = a_copresenter_talk_host()
    private.answers.deltas = ["Der Anfang. ", "Der Satz."]
    lobby.sign_in()
    refused_lobby, refused_private, _ = a_copresenter_talk_host()
    refused_private.answers.unavailable = CoPresenterUnavailableError()
    refused_lobby.sign_in()

    answered = _payloads_by_event(ask(lobby.client).text)
    refused = _payloads_by_event(ask(refused_lobby.client).text)

    assert {"text", "sentence", "audio", "done"} <= answered.keys()
    assert all(
        payload["ai_generated"] is True
        for payloads in answered.values()
        for payload in payloads
    )
    assert refused.keys() == {"error"}
    assert "ai_generated" not in refused["error"][0]


def _payloads_by_event(stream: str) -> dict[str, list[dict[str, object]]]:
    by_event: dict[str, list[dict[str, object]]] = {}
    for name, data in re.findall(r"^event: (\w+)\ndata: (.*)$", stream, re.MULTILINE):
        by_event.setdefault(name, []).append(json.loads(data))
    return by_event
