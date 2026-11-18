"""The `agent` section of the configuration: the client's own."""

from typing import Final, cast

from agents.config import AgentConfig
from pydantic import model_validator

# The previous release requires this setting, and its deployment still sets it,
# so its name starts the instance and its value is never read.
RETIRED_BACKEND_SETTING: Final = "backend"


class AgentSettings(AgentConfig):
    """The client's settings, with the one retired name dropped unread."""

    @model_validator(mode="before")
    @classmethod
    def drop_the_retired_backend(cls, given: object) -> object:
        """Leave the retired name out before the client refuses unknown names."""
        if not isinstance(given, dict):
            return given
        named = cast("dict[str, object]", given)
        return {
            name: value
            for name, value in named.items()
            if name != RETIRED_BACKEND_SETTING
        }


__all__ = ["AgentSettings"]
