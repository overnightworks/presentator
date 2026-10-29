"""Who the session gate admitted, for the page, the JSON route and the socket.

webauth's gate decides every request; these helpers turn its answer into the
lobby's own `User` (and the session it rode in on), so no route needs the
renderer or the sign-in wiring to know the person.
"""

from dataclasses import dataclass

from fastapi import Request
from starlette.requests import HTTPConnection
from webauth.config import web_auth_config
from webauth.dependencies import (
    ConnectionRefusal,
    admit_session,
    session_id_from_request,
)
from webauth.middleware.session_gate import admitted_user
from webauth.ports import AuthenticatedUser, SessionRecordStore

from presentator.contracts.models import Role, User


def user_of_admitted(admitted: AuthenticatedUser) -> User:
    """The lobby's person for the account the library admitted."""
    return User(id=admitted.id, username=admitted.username, role=Role(admitted.role))


def signed_in_person(request: Request) -> User | None:
    """Who the session gate admitted, or nobody on the pages that need no session."""
    admitted = admitted_user(request)
    return None if admitted is None else user_of_admitted(admitted)


def gated_person(request: Request) -> User:
    """Who the gate admitted on a gated route; a route it let past fails loud."""
    person = signed_in_person(request)
    if person is None:
        message = "The gate admitted nobody on a route that needs a person."
        raise RuntimeError(message)
    return person


def gated_session_id(request: Request) -> str:
    """The session the gate admitted on a gated route, so a secret binds to it."""
    person = signed_in_person(request)
    session_id = session_id_from_request(request, web_auth_config(request))
    if person is None or session_id is None:
        message = "The gate admitted no session on a route that needs one."
        raise RuntimeError(message)
    return session_id


@dataclass(frozen=True, slots=True, kw_only=True)
class SignedInConnection:
    """The hash-free person and authoritative session behind one connection."""

    person: User
    session_id: str

    @property
    def user_id(self) -> str:
        """The signed-in person's account id."""
        return self.person.id


def signed_in_connection(
    connection: HTTPConnection,
    sessions: SessionRecordStore,
) -> SignedInConnection | None:
    """Resolve one HTTP or WebSocket connection through the library's one decider."""
    config = web_auth_config(connection)
    session_id = session_id_from_request(connection, config)
    if session_id is None:
        return None
    admitted = admit_session(session_id, sessions=sessions, config=config)
    if isinstance(admitted, ConnectionRefusal):
        return None
    return SignedInConnection(person=user_of_admitted(admitted), session_id=session_id)
