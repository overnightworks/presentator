"""What the build worker reads of its environment; it loads nothing of the web's."""

import threading
from typing import Final

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from presentator.host.environment import ENVIRONMENT_PREFIX, cannot_start, variable_of

# The shortest key of the build worker's hello, the web's file and the worker's alike.
MINIMUM_KEY_LENGTH: Final = 32
# What the web and the build worker both bound one build step by.
DEFAULT_BUILD_TIMEOUT_SECONDS: Final = 300.0
# What the web and the build worker both bound one build's output by.
DEFAULT_BUILD_OUTPUT_MEGABYTES: Final = 300
# The watchdog cannot be armed for longer than threading.TIMEOUT_MAX, and the
# worker's fixed phases and the clock offset come on top of the build step.
LONGEST_BUILD_TIMEOUT_SECONDS: Final = threading.TIMEOUT_MAX / 2


class WorkerSettings(BaseSettings):
    """What the build worker reads of its container's environment, and no file."""

    model_config = SettingsConfigDict(env_prefix=ENVIRONMENT_PREFIX, frozen=True)

    build_timeout_seconds: float = Field(
        default=DEFAULT_BUILD_TIMEOUT_SECONDS,
        gt=0,
        le=LONGEST_BUILD_TIMEOUT_SECONDS,
        allow_inf_nan=False,
    )
    build_output_megabytes: int = Field(default=DEFAULT_BUILD_OUTPUT_MEGABYTES, gt=0)


def worker_settings_variables() -> frozenset[str]:
    """The environment variables the worker reads, derived from its fields."""
    return frozenset(variable_of(name) for name in WorkerSettings.model_fields)


def load_worker_settings() -> WorkerSettings:
    """Read the worker's settings, refusing a value its limits cannot hold."""
    try:
        return WorkerSettings.model_validate({})
    except ValidationError as refused:
        # `msg` states the rule that failed and never quotes the value.
        faults = [
            (variable_of(str(fault["loc"][0])), fault["msg"])
            for fault in refused.errors()
        ]
        raise cannot_start(
            ", ".join(f"{variable}: {rule}" for variable, rule in faults),
            variables=tuple(variable for variable, _ in faults),
        ) from None
