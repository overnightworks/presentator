"""The co-presenter agent's client configuration."""

from __future__ import annotations

from agent_runtime_client.config import AgentConfig


def agent_config(*, backend: str, service_url: str, workload_scope: str) -> AgentConfig:
    """The client's `agent` section, from what the host's settings hold."""
    return AgentConfig.model_validate(
        {
            "backend": backend,
            "service_url": service_url,
            "workload_scope": workload_scope,
        }
    )
