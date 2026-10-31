"""The privacy section states the numbers the code keeps."""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from webauth.oidc import PENDING_LOGIN_MAX_AGE_SECONDS

from presentator.application.build_retention import REMOVED_DECK_KEPT
from presentator.application.decks import DRAFT_MAX_AGE
from presentator.application.identity import SESSION_LIFETIME, SESSION_SWEEP_INTERVAL

_SECTION = Path(__file__).parents[3] / "legal" / "datenschutz.html"
_DAY = timedelta(days=1)
_HOUR = timedelta(hours=1)
_MINUTE = timedelta(minutes=1)


@pytest.mark.parametrize(
    ("sentence", "kept"),
    [
        (
            r"Cookie nach der Anmeldung ist (\d+) Stunden gültig",
            SESSION_LIFETIME / _HOUR,
        ),
        (
            r"Eine Sitzung endet (\d+) Stunden nach der Anmeldung",
            SESSION_LIFETIME / _HOUR,
        ),
        (
            r"abgelaufene Sitzungen löscht der Dienst alle (\d+) Minuten",
            SESSION_SWEEP_INTERVAL / _MINUTE,
        ),
        (r"rund (\d+) Stunden nach Erzeugung", DRAFT_MAX_AGE / _HOUR),
        (r"(\d+) Tage nach dem ersten Abgleich", REMOVED_DECK_KEPT / _DAY),
        (
            r"nur über HTTPS, (\d+) Minuten gültig",
            timedelta(seconds=PENDING_LOGIN_MAX_AGE_SECONDS) / _MINUTE,
        ),
        (
            r"bleibt es, bis seine (\d+) Minuten ablaufen",
            timedelta(seconds=PENDING_LOGIN_MAX_AGE_SECONDS) / _MINUTE,
        ),
    ],
)
def test_a_number_the_section_states_equals_the_code_that_keeps_it(
    sentence: str, kept: float
) -> None:
    stated = re.search(sentence, _SECTION.read_text(encoding="utf-8"))

    assert stated is not None
    assert int(stated.group(1)) == kept
