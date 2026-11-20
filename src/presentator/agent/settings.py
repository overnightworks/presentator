"""The `agent` section of the configuration: the client's own."""

from agents.config import AgentConfig


class AgentSettings(AgentConfig):
    """The client's settings; a name the client does not own is refused."""


__all__ = ["AgentSettings"]
