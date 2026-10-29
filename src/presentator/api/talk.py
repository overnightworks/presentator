"""The talk host: an origin that signs people in by itself and serves their talks.

A deck is code, so its talk runs on a site of its own where the lobby's cookies
never reach. The talk host answers no lobby route: it starts the sign-in at the
same issuer, completes it at its own callback, and serves the signed-in
owner's built talk and, to any signed-in person, the co-presenter that talk
speaks to. It has no page of its own, so a refused sign-in is answered by a
redirect to the lobby's refusal route.
"""

from contextlib import nullcontext
from http import HTTPStatus
from typing import Final

from fastapi import FastAPI, Request, Response
from webauth.config import install_web_auth_config
from webauth.middleware.session_gate import SessionGate
from webauth.middleware.stack import install_web_auth_middleware
from webauth.policies import (
    BodySizePolicy,
    CacheControlRule,
    CspDirective,
    CsrfPolicy,
    PathRules,
    PublicRule,
    SecurityHeadersPolicy,
    default_content_security_policy,
)
from webauth.ports import SessionRecordStore
from webauth.sign_in_routes import SignInRefusal, install_sign_in_routes

from presentator.api.auth import (
    DEFAULT_BODY_BYTES,
    TALK_SIGN_IN,
    InstalledAuth,
    signed_in_connection,
    talk_sign_in_refusal_path,
)
from presentator.api.copresenter import CoPresenterSurface, add_copresenter_routes
from presentator.api.decks import add_talk_mount
from presentator.api.health import HEALTH_IS_PUBLIC, add_health_route
from presentator.application.decks import Decks

TALK_PERMISSIONS_POLICY: Final = "camera=(), microphone=(self), geolocation=()"
_ROOT: Final = "/"


def talk_content_security_policy() -> str:
    """The one policy every answer of the talk host carries.

    A built Slidev talk inlines its styles and loads everything else from its
    own build directory, so no other host is named: no font host, no form
    target, no frame.
    """
    return default_content_security_policy(
        [],
        added_sources={CspDirective.STYLE_SRC: ["'unsafe-inline'"]},
    )


def _install_talk_middleware(
    talk: FastAPI, auth: InstalledAuth, sessions: SessionRecordStore
) -> None:
    install_web_auth_middleware(
        talk,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=auth.content_security_policy,
            permissions_policy=TALK_PERMISSIONS_POLICY,
            cache_control_rules=(
                CacheControlRule(PathRules(prefixes=(_ROOT,)), "no-store"),
            ),
        ),
        body_size=BodySizePolicy(default_max_bytes=DEFAULT_BODY_BYTES),
        csrf_origin=CsrfPolicy(),
        session_gate=SessionGate(
            session_transaction=lambda: nullcontext(sessions),
            login_redirect=TALK_SIGN_IN.login_redirect,
            public=(
                PublicRule(PathRules(exact=frozenset({_ROOT}))),
                *TALK_SIGN_IN.public_rules,
                HEALTH_IS_PUBLIC,
            ),
        ),
    )


def create_talk(
    *,
    sessions: SessionRecordStore,
    decks: Decks,
    auth: InstalledAuth,
    lobby_origin: str,
    copresenter: CoPresenterSurface | None = None,
) -> FastAPI:
    """Build the talk host around the same stores and use cases as the lobby."""

    def refused(_request: Request, refusal: SignInRefusal) -> Response:
        answer = Response(status_code=HTTPStatus.SEE_OTHER)
        answer.headers["location"] = (
            f"{lobby_origin}{talk_sign_in_refusal_path(refusal.page)}"
        )
        return answer

    def to_the_lobby() -> Response:
        answer = Response(status_code=HTTPStatus.SEE_OTHER)
        answer.headers["location"] = lobby_origin
        return answer

    talk = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_web_auth_config(talk, auth.config)
    _install_talk_middleware(talk, auth, sessions)
    add_health_route(talk)
    talk.add_api_route(_ROOT, to_the_lobby, methods=["GET"])
    install_sign_in_routes(
        talk,
        paths=TALK_SIGN_IN,
        relying_party=lambda _request: auth.relying_party,
        admission=auth.admission,
        transaction=auth.transaction,
        refused=refused,
        keeps_id_token=False,
        max_sessions=None,
        logout_button_label=None,
    )
    add_talk_mount(talk, decks=decks)
    if copresenter is not None:
        add_copresenter_routes(
            talk,
            surface=copresenter,
            admission=lambda connection: signed_in_connection(connection, sessions),
        )
    return talk
