"""The registry a stopping pod waits on for the talks that are open."""

from __future__ import annotations

import asyncio

from presentator.api.copresenter import OpenTalks
from tests.presentator.agent.support.driving import on_an_event_loop


@on_an_event_loop
async def test_no_open_talk_is_closed_at_once() -> None:
    await asyncio.wait_for(OpenTalks().closed(), timeout=1)


@on_an_event_loop
async def test_the_wait_returns_only_once_the_open_talk_has_closed() -> None:
    talks = OpenTalks()
    with talks.open():
        waiting = asyncio.create_task(talks.closed())
        for _ in range(3):
            await asyncio.sleep(0)
        assert not waiting.done()
    await asyncio.wait_for(waiting, timeout=1)
