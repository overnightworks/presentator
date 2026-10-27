"""Drive the two-step source removal the way a browser does."""

import re

from fastapi.testclient import TestClient
from httpx2 import Response

from presentator.api.sources import REMOVE, REMOVE_CONFIRMED


def ask_removal(
    client: TestClient,
    name: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    return client.post(REMOVE.format(name=name), headers=headers)


DECK_COUNT_FIELD = re.compile(r'name="deck_count" value="(\d+)"')
RUN_COUNT_FIELD = re.compile(r'name="run_count" value="(\d+)"')


def confirm_removal(
    client: TestClient,
    name: str,
    *,
    headers: dict[str, str] | None = None,
) -> Response:
    """Confirm a removal the way a person who just saw the ask step would.

    The confirm form's hidden counts are read off the ask step's own answer
    first, exactly as a browser would carry them; an ask that was refused or
    found nothing leaves none to carry, and the confirmed POST answers that
    the same way on its own.
    """
    asked = ask_removal(client, name, headers=headers)
    data: dict[str, str] = {}
    deck_count = DECK_COUNT_FIELD.search(asked.text)
    run_count = RUN_COUNT_FIELD.search(asked.text)
    if deck_count is not None:
        data["deck_count"] = deck_count.group(1)
    if run_count is not None:
        data["run_count"] = run_count.group(1)
    return client.post(REMOVE_CONFIRMED.format(name=name), data=data, headers=headers)
