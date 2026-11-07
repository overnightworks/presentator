"""The `agent` section of the configuration: the client's own, plus the MCP block."""

import re
from typing import Annotated, Final

from agent_runtime_client.config import AgentConfig
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from webauth.admission import AdmissionConfig, NoAdmins

# RFC 6749 section 3.3: one or more printable ASCII characters but space, `"` and `\`.
_SCOPE_TOKEN: Final = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")
_ONE_SCOPE: Final = "is exactly one scope token"


def _a_group_path(given: str) -> str:
    """Refuse what webauth's admission would not take as a group path."""
    AdmissionConfig(
        bound_issuer="-", login_group=given, admin_group=NoAdmins.IN_THIS_APP
    )
    return given


def _one_scope_token(given: str) -> str:
    if _SCOPE_TOKEN.fullmatch(given) is None:
        raise ValueError(_ONE_SCOPE)
    return given


class McpSettings(BaseModel):
    """The agents client of `/mcp` and the groups and scope its token holds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    client_id: str = Field(min_length=1)
    group: Annotated[str, AfterValidator(_a_group_path)]
    agent_group: Annotated[str, AfterValidator(_a_group_path)]
    scope: Annotated[str, AfterValidator(_one_scope_token)]


class AgentSettings(AgentConfig):
    """The client's own settings; the MCP block has all four values or none."""

    mcp: McpSettings | None = None
