"""Runs a test written as a coroutine on its own event loop."""

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any


def on_an_event_loop[**Parameters](
    test: Callable[Parameters, Coroutine[Any, Any, None]],
) -> Callable[Parameters, None]:
    """The test as a plain function that runs the coroutine to its end."""

    @functools.wraps(test)
    def run(*args: Parameters.args, **kwargs: Parameters.kwargs) -> None:
        asyncio.run(test(*args, **kwargs))

    return run
