"""The privacy section states the numbers the code keeps."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from webauth.oidc import PENDING_LOGIN_MAX_AGE_SECONDS

from presentator.application.decks import DRAFT_MAX_AGE
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL

_SECTION = Path(__file__).parents[3] / "legal" / "datenschutz.html"
_HOUR = timedelta(hours=1)
_MINUTE = timedelta(minutes=1)


@pytest.mark.parametrize(
    ("unit", "kept"),
    [
        ("Stunden", {SESSION_LIFETIME / _HOUR, DRAFT_MAX_AGE / _HOUR}),
        (
            "Minuten",
            {
                SESSION_SWEEP_INTERVAL / _MINUTE,
                timedelta(seconds=PENDING_LOGIN_MAX_AGE_SECONDS) / _MINUTE,
            },
        ),
    ],
)
def test_every_number_the_section_states_equals_the_code_that_keeps_it(
    unit: str, kept: set[float]
) -> None:
    text = _SECTION.read_text(encoding="utf-8")

    stated = {int(number) for number in re.findall(rf"(\d+) {unit}", text)}

    assert stated == kept
