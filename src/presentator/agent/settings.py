"""The `agent` section of the configuration: the client's own."""

from typing import cast

from agents.config import AgentConfig, ChatGateway
from pydantic import BaseModel, ConfigDict, model_validator


class AgentSettings(BaseModel):
    """What `PRESENTATOR_AGENT__*` sets: the gateway; any other name is refused."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    gateway: ChatGateway

    @model_validator(mode="before")
    @classmethod
    def _without_the_retired_api_group(cls, given: object) -> object:
        # The platform keeps handing `PRESENTATOR_AGENT__API_GROUP` until it
        # removes the key; refusing it would crash-loop the pod, so only this
        # one key is dropped and every other stale key stays refused.
        if isinstance(given, dict):
            section = cast("dict[str, object]", given)
            return {key: value for key, value in section.items() if key != "api_group"}
        return given

    def configured(self, api_group: object) -> AgentConfig:
        """The client's whole configuration, its group the realm's."""
        return AgentConfig(gateway=self.gateway, api_group=str(api_group))


__all__ = ["AgentSettings"]
