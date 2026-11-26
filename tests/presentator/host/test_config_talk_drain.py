"""The drain's limit: a positive, finite number of seconds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from presentator.host.config import Settings
from tests.support.lobby import a_settings


def _settings_with(**overrides: object) -> Settings:
    return Settings.model_validate({**a_settings().model_dump(), **overrides})


@pytest.mark.parametrize("refused", [0, -1, float("inf"), float("nan")])
def test_a_drain_limit_that_is_not_above_zero_or_not_finite_is_refused(
    refused: float,
) -> None:
    with pytest.raises(ValidationError) as error:
        _settings_with(talk_drain_seconds=refused)

    assert "talk_drain_seconds" in str(error.value)


@pytest.mark.parametrize("seconds", [0.5, 12])
def test_a_drain_limit_above_zero_is_the_limit_the_settings_hold(
    seconds: float,
) -> None:
    assert _settings_with(talk_drain_seconds=seconds).talk_drain_seconds == seconds
