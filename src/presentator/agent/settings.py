"""The `agent` section of the configuration: the client's own."""

from agents.config import AgentConfig, ChatGateway
from pydantic import BaseModel, ConfigDict
from webauth.admission import GroupPath


class AgentSettings(BaseModel):
    """What `PRESENTATOR_AGENT__*` sets: the gateway; any other name is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway: ChatGateway

    def configured(self, api_group: GroupPath) -> AgentConfig:
        """The client's whole configuration, its group the realm's."""
        return AgentConfig(gateway=self.gateway, api_group=str(api_group))


__all__ = ["AgentSettings"]
