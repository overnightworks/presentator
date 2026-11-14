"""The role word webauth's store contract writes, read as a role this product has."""

from presentator.contracts.models import Role

_ROLE_THE_KIT_WRITES = "member"


def role_stored_for(role: str) -> str:
    """The one role a member has here.

    The kit creates its accounts with the role "member", which this product's
    closed `Role` refuses; webauth itself only ever writes "admin" and "user".
    """
    return Role.USER.value if role == _ROLE_THE_KIT_WRITES else role
