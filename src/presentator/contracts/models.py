"""The identity types every layer of the login speaks."""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    """An instance has only these two roles."""

    ADMIN = "admin"
    USER = "user"


@dataclass(frozen=True, slots=True, kw_only=True)
class User:
    """Who may log in."""

    id: str
    username: str
    role: Role

    @property
    def is_admin(self) -> bool:
        """Only an admin reaches Settings and the instance defaults."""
        return self.role is Role.ADMIN
