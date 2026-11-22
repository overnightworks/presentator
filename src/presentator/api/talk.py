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
from starlette.middleware import Middleware
from webauth.admission import AdmissionConfig
from webauth.config import WebAuthConfig
from webauth.middleware.session_gate import SessionGate
from webauth.oidc import OidcClientConfig
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
from webauth.ports import SessionRecordStore, SignInTransaction
from webauth.sign_in_routes import SignInRefusal
from webauth.web_auth import (
    IssuerCalls,
    ServedBehind,
    SignInPages,
    WebAuth,
    WebAuthSettings,
    WebAuthStores,
)

from presentator.api.admission import signed_in_connection
from presentator.api.auth import (
    DEFAULT_BODY_BYTES,
    TALK_RATE_LIMIT,
    TALK_SIGN_IN,
    talk_sign_in_refusal_path,
)
from presentator.api.copresenter import CoPresenterSurface, add_copresenter_routes
from presentator.api.copresenter_talk import add_copresenter_talk_route
from presentator.api.decks import add_talk_mount
from presentator.api.health import (
    HEALTH_IS_PUBLIC,
    Readiness,
    add_health_route,
    always_ready,
)
from presentator.api.json_bodies import StorableJsonBodies
from presentator.api.problems import install_error_answers
from presentator.api.session_api import language_of
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


def _talk_security_headers() -> SecurityHeadersPolicy:
    """The headers every answer of the talk host carries."""
    return SecurityHeadersPolicy(
        content_security_policy=talk_content_security_policy(),
        permissions_policy=TALK_PERMISSIONS_POLICY,
        cache_control_rules=(
            CacheControlRule(PathRules(prefixes=(_ROOT,)), "no-store"),
        ),
    )


def talk_web_auth(  # noqa: PLR0913 - the host's one call names each input of the talk host's auth
    *,
    config: WebAuthConfig,
    client: OidcClientConfig,
    admission: AdmissionConfig,
    sign_in: SignInTransaction,
    sessions: SessionRecordStore,
    lobby_origin: str,
    issuer_calls: IssuerCalls | None = None,
) -> WebAuth:
    """The talk host's auth, built before its routes and installed after the last one.

    The talk host has no page of its own, so a refused sign-in is answered by a
    redirect to the lobby's refusal route.
    """

    def refused(_request: Request, refusal: SignInRefusal) -> Response:
        answer = Response(status_code=HTTPStatus.SEE_OTHER)
        answer.headers["location"] = (
            f"{lobby_origin}{talk_sign_in_refusal_path(refusal.page)}"
        )
        return answer

    return WebAuth.build(
        WebAuthSettings(
            config=config,
            client=client,
            sign_in_paths=TALK_SIGN_IN,
            keeps_id_token=False,
            max_sessions=None,
            security_headers=_talk_security_headers(),
            csrf_origin=CsrfPolicy(),
            body_size=BodySizePolicy(default_max_bytes=DEFAULT_BODY_BYTES),
            rate_limit=TALK_RATE_LIMIT,
        ),
        stores=WebAuthStores(sign_in=sign_in, sessions=lambda: nullcontext(sessions)),
        groups=admission,
        pages=SignInPages(
            refused=refused,
            logout_page_text=None,
            ui_language=lambda request: language_of(request, sessions),
        ),
        front=ServedBehind.SHARED_FRONT,
        gate=SessionGate(
            login_redirect=TALK_SIGN_IN.login_redirect,
            public=(
                PublicRule(PathRules(exact=frozenset({_ROOT}))),
                HEALTH_IS_PUBLIC,
            ),
        ),
        # Added inside the gate, so a body is read once the person is known.
        inside=(Middleware(StorableJsonBodies),),
        issuer_calls=issuer_calls,
    )


def create_talk(  # noqa: PLR0913 - the composition root's one call names each owner
    *,
    sessions: SessionRecordStore,
    decks: Decks,
    web_auth: WebAuth,
    lobby_origin: str,
    copresenter: CoPresenterSurface | None = None,
    readiness: Readiness = always_ready,
) -> FastAPI:
    """Build the talk host around the same stores and use cases as the lobby."""

    def to_the_lobby() -> Response:
        answer = Response(status_code=HTTPStatus.SEE_OTHER)
        answer.headers["location"] = lobby_origin
        return answer

    talk = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_error_answers(talk)
    add_health_route(talk, readiness)
    talk.add_api_route(_ROOT, to_the_lobby, methods=["GET"])
    add_talk_mount(talk, decks=decks)
    if copresenter is not None:
        add_copresenter_routes(
            talk,
            surface=copresenter,
            admission=lambda connection: signed_in_connection(connection, sessions),
            sessions=sessions,
        )
        add_copresenter_talk_route(
            talk,
            surface=copresenter,
            admission=lambda connection: signed_in_connection(connection, sessions),
            sessions=sessions,
        )
    web_auth.install(talk, bearer=None)
    return talk
