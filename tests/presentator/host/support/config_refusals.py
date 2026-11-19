"""What a refused configuration says: its field, and the values it never quotes."""

import re
from collections.abc import Callable

import pytest

from presentator.host.environment import ConfigurationError


def assert_refusal_names(load: Callable[[], object], field: str) -> None:
    with pytest.raises(ConfigurationError, match=re.escape(field)):
        load()


def assert_refusal_never_quotes(load: Callable[[], object], *values: str) -> None:
    with pytest.raises(ConfigurationError) as refused:
        load()

    for value in values:
        assert value not in str(refused.value)
