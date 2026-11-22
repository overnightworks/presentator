"""How a process names its settings in the environment and refuses them."""

from typing import Final

ENVIRONMENT_PREFIX: Final = "PRESENTATOR_"
# The realm's names are the platform's, handed to every app under these keys.
REALM_PREFIX: Final = "REALM_"
_REFUSED: Final = "this environment cannot start an instance"


class ConfigurationError(ValueError):
    """What the environment carries is not a configuration to run on.

    `variables` names the environment variables that were refused, when it is
    known which; they are setting names, never values.
    """

    def __init__(self, message: str, *, variables: tuple[str, ...] = ()) -> None:
        """Say why, and which variables."""
        super().__init__(message)
        self.variables = variables


def variable_of(field_name: str, *, prefix: str = ENVIRONMENT_PREFIX) -> str:
    """The environment variable that carries a settings field."""
    return f"{prefix}{field_name}".upper()


def cannot_start(reason: str, *, variables: tuple[str, ...] = ()) -> ConfigurationError:
    """The refusal an environment no instance can run on is answered with."""
    return ConfigurationError(f"{_REFUSED}: {reason}", variables=variables)
