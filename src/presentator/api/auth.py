"""The pages that sign a person in and out, and the session gate on everything else.

webauth's `SessionGate` decides every request: a gated address
answers the redirect to the start of the sign-in (or 401 to a JSON caller) while
nobody is signed in, the public and optional addresses below are the
exceptions, a form another site submitted is refused, and no answer may be
replayed from the browser cache (issue #8, lines 11 to 15).
"""

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from http import HTTPMethod, HTTPStatus
from pathlib import Path
from typing import Final, cast

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from webauth.config import WebAuthConfig, install_web_auth_config
from webauth.middleware.bearer_gate import BearerGate, PersonPaths
from webauth.middleware.session_gate import SessionGate
from webauth.middleware.stack import install_web_auth_middleware
from webauth.oidc import OidcRelyingParty
from webauth.oidc_login import AdmissionConfig
from webauth.policies import (
    AuthorizedParties,
    BodySizePolicy,
    BodySizeRule,
    CacheControlRule,
    CsrfPolicy,
    PathRules,
    PublicRule,
    SecurityHeadersPolicy,
    default_content_security_policy,
)
from webauth.ports import SessionRecordStore, SignInTransaction
from webauth.sign_in_routes import (
    RefusalPage,
    SignInPaths,
    install_sign_in_routes,
)

from presentator.api.admission import gated_person
from presentator.api.decks import add_deck_pages
from presentator.api.health import HEALTH_IS_PUBLIC, add_health_route
from presentator.api.hooks import HOOK_CALLS, fetch_hook
from presentator.api.pages import TEMPLATES, Pages, state_word
from presentator.api.people import ADMIN_PREFIX, AdminPeople, people_routes
from presentator.api.preferences import preference_routes
from presentator.api.session_api import SESSION_PATH, SessionAddresses, session_routes
from presentator.api.sources import source_routes
from presentator.application.decks import Decks
from presentator.application.voice import VoiceStatusUse
from presentator.contracts.text import LobbyText

_STATIC_DIR: Final = Path(__file__).parent / "static"
_STATIC_PATH: Final = "/static"
_INSIDE_STATIC: Final = f"{_STATIC_PATH}/"
_LOBBY: Final = "/"
LOGIN_PATH: Final = "/login"
START_SIGN_IN_PATH: Final = "/login/oidc"
SIGN_IN_CALLBACK_PATH: Final = "/auth/callback"
BACKCHANNEL_LOGOUT_PATH: Final = "/auth/backchannel-logout"
LOGOUT_PATH: Final = "/logout"
LOBBY_SIGN_IN: Final = SignInPaths(
    start=START_SIGN_IN_PATH,
    callback=SIGN_IN_CALLBACK_PATH,
    logout=LOGOUT_PATH,
    backchannel_logout=BACKCHANNEL_LOGOUT_PATH,
)
TALK_SIGN_IN: Final = SignInPaths(
    start=START_SIGN_IN_PATH, callback=SIGN_IN_CALLBACK_PATH
)
_SETUP: Final = "/setup"


_REFUSAL_PAGES: Final = {
    RefusalPage.NO_ACCESS: "no_access.html",
    RefusalPage.LOGIN_FAILED: "login_failed.html",
}


def talk_sign_in_refusal_path(refusal: RefusalPage) -> str:
    """The lobby's address that shows a talk sign-in refusal."""
    return f"/login/talk/{refusal.value}"


_PERSON_API: Final = "/api/"

# Signing in (its start and the issuer's return) and signing out are the
# addresses the gate leaves alone, and the library derives them from the
# sign-in paths; logging out ends a session rather than using one. The theme
# stylesheet has to render the open pages themselves, so it is public too, and
# so is the address another server posts to: a source host's POST under the
# hook prefix. Keycloak's back-channel logout is open and exempt from the
# origin check through the sign-in paths.
# `/setup` is a retired address: it answers 404 to everyone, so a signed-out
# reader is not sent to a login for a page that does not exist.
_SERVER_CALLS: Final = PathRules(prefixes=(HOOK_CALLS,))
_PUBLIC_RULES: Final = (
    PublicRule(
        PathRules(
            exact=frozenset(
                {
                    LOGIN_PATH,
                    _SETUP,
                    *(talk_sign_in_refusal_path(refusal) for refusal in RefusalPage),
                }
            )
        )
    ),
    PublicRule(PathRules(prefixes=(_INSIDE_STATIC,))),
    HEALTH_IS_PUBLIC,
    PublicRule(_SERVER_CALLS, methods=frozenset({HTTPMethod.POST})),
    *LOBBY_SIGN_IN.public_rules,
)
PERMISSIONS_POLICY: Final = "camera=(), microphone=(), geolocation=()"
_CSRF_POLICY: Final = CsrfPolicy(
    exempt=PathRules(
        exact=LOBBY_SIGN_IN.backchannel.exact, prefixes=_SERVER_CALLS.prefixes
    )
)
DEFAULT_BODY_BYTES: Final = 32 * 1024
# Every route has this budget, forms and the ask included; the hook keeps a git
# host's push budget, since GitHub documents payloads of up to 25 MB.
_BODY_SIZE: Final = BodySizePolicy(
    default_max_bytes=DEFAULT_BODY_BYTES,
    rules=(
        BodySizeRule(
            PathRules(prefixes=(HOOK_CALLS,)),
            max_bytes=25 * 1024 * 1024,
            methods=frozenset({HTTPMethod.POST}),
        ),
        LOBBY_SIGN_IN.backchannel_body_size_rule,
    ),
)
LOGIN_REDIRECT: Final = LOBBY_SIGN_IN.login_redirect
# Jinja's stubs type the globals as its own defaults only, so the dict is
# named as the mixed bag it is.
cast("dict[str, object]", TEMPLATES.env.globals).update(
    login_path=LOGIN_PATH,
    logout_path=LOGOUT_PATH,
)


def content_security_policy() -> str:
    """The one policy every answer of the lobby carries, except the logout page.

    Nothing in the lobby needs an inline script, an inline style, an external
    script host, or a frame: every asset ships under `/static`, served from this
    origin. The logout page brings its own policy, which webauth's security
    headers leave in place.
    """
    return default_content_security_policy([])


@dataclass(frozen=True, slots=True, kw_only=True)
class InstalledAuth:
    """The library configuration this host runs the lobby with."""

    config: WebAuthConfig
    relying_party: OidcRelyingParty
    admission: AdmissionConfig
    transaction: SignInTransaction
    content_security_policy: str
    # The talk host signs in through its own installation of the same wiring;
    # the lobby sends a refused talk sign-in's retry there.
    talk_origin: str

    @property
    def talk_sign_in_address(self) -> str:
        """Where a sign-in starts at the talk host."""
        return f"{self.talk_origin}{START_SIGN_IN_PATH}"


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyPrivate:
    """Private capabilities the authenticated lobby may expose to an admin."""

    voice: VoiceStatusUse
    # Absent: no token is admitted, so neither the gate nor the route exists.
    admin_people: AdminPeople | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeckRow:
    """One deck as the list renders it: its state, name, folder, and age."""

    title: str
    slug: str
    state: str
    state_word: str
    changed: str


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The lobby's HTML answers, each one asking the use cases what is true."""

    decks: Decks
    pages: Pages
    auth: InstalledAuth

    def home(self, request: Request) -> Response:
        """List the signed-in person's decks, newest first."""
        viewer = gated_person(request).id
        return self.pages.page(
            request,
            "home.html",
            source_address=self.decks.source_address(viewer=viewer),
            decks=self._rows(self.pages.appearance(request).text, viewer),
        )

    def _rows(self, text: LobbyText, viewer: str) -> tuple[DeckRow, ...]:
        return tuple(
            DeckRow(
                title=deck.title,
                slug=deck.slug,
                state=deck.state.value,
                state_word=state_word(deck.state, text),
                changed=self.pages.age_in_words(deck.age, text.language_tag),
            )
            for deck in self.decks.listed(viewer=viewer)
        )

    def login_page(self, request: Request) -> Response:
        """Show the signed-out page, whose one button starts the sign-in."""
        return self.pages.page(request, "login.html", start_sign_in=START_SIGN_IN_PATH)

    def talk_sign_in_refusal_page(
        self, refusal: RefusalPage
    ) -> Callable[[Request], Response]:
        """The route that shows why the talk host refused a sign-in.

        Its retry restarts the sign-in at the talk host, and the page is one
        address per refusal so no request chooses what it says.
        """

        def show(request: Request) -> Response:
            return self.refused_page(
                request,
                refusal,
                retry=self.auth.talk_sign_in_address,
            )

        return show

    def refused_page(
        self, request: Request, refusal: RefusalPage, *, retry: str
    ) -> Response:
        """A refusal's page and where to retry; the library adds the cookie deletion."""
        return self.pages.page(
            request,
            _REFUSAL_PAGES[refusal],
            status=HTTPStatus.FORBIDDEN,
            start_sign_in=retry,
        )


def _install_middleware(
    lobby: FastAPI,
    auth: InstalledAuth,
    sessions: SessionRecordStore,
    admin: AdminPeople | None,
) -> None:
    """Install the library's stack once, in its own order.

    Every answer, including the gate's redirect and a refusal, carries the
    security headers. The gate's store calls commit on their own and `touch` is
    one update-only statement (ADR 0003), so the gate's admission takes no lock.
    The admin prefix, when it is served, takes a bearer token and no cookie.
    While the admin prefix is served, `/api/` takes a person's token as that
    person; a request without a token is decided by the session cookie.
    """
    install_web_auth_middleware(
        lobby,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=auth.content_security_policy,
            permissions_policy=PERMISSIONS_POLICY,
            cache_control_rules=(
                CacheControlRule(PathRules(prefixes=("/",)), "no-store"),
            ),
        ),
        body_size=_BODY_SIZE,
        bearer_gate=None if admin is None else _bearer_gate(auth, admin),
        csrf_origin=_CSRF_POLICY,
        session_gate=SessionGate(
            session_transaction=lambda: nullcontext(sessions),
            login_redirect=LOGIN_REDIRECT,
            public=_PUBLIC_RULES,
            optional=PathRules(exact=frozenset({SESSION_PATH})),
        ),
    )


def _bearer_gate(auth: InstalledAuth, admin: AdminPeople) -> BearerGate:
    return BearerGate(
        paths=PathRules(prefixes=(ADMIN_PREFIX,)),
        relying_party=auth.relying_party,
        admission=auth.admission,
        authorized_parties=AuthorizedParties(admin.admitted_clients),
        person_paths=PersonPaths(PathRules(prefixes=(_PERSON_API,)), auth.transaction),
    )


def _session_routes(pages: Pages, auth: InstalledAuth) -> APIRouter:
    return session_routes(
        preferences=pages.preferences,
        addresses=SessionAddresses(
            start=START_SIGN_IN_PATH,
            logout=LOGOUT_PATH,
            talk_start=auth.talk_sign_in_address,
        ),
    )


def create_lobby(
    *,
    sessions: SessionRecordStore,
    decks: Decks,
    pages: Pages,
    auth: InstalledAuth,
    private: LobbyPrivate,
) -> FastAPI:
    """Build the lobby around the use cases and the adapters the host chose.

    The fetch hook always stands under `/sources/`; a call that cannot name a
    stored source and its secret is answered alike, and GET stays behind the
    session.
    """
    surfaces = _Surfaces(
        decks=decks,
        pages=pages,
        auth=auth,
    )
    lobby = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_web_auth_config(lobby, auth.config)
    _install_middleware(lobby, auth, sessions, private.admin_people)
    lobby.add_api_route(_LOBBY, surfaces.home, methods=["GET"])
    lobby.add_api_route(LOGIN_PATH, surfaces.login_page, methods=["GET"])
    install_sign_in_routes(
        lobby,
        paths=LOBBY_SIGN_IN,
        relying_party=lambda _request: auth.relying_party,
        admission=auth.admission,
        transaction=auth.transaction,
        refused=lambda request, refusal: surfaces.refused_page(
            request, refusal.page, retry=START_SIGN_IN_PATH
        ),
        keeps_id_token=True,
        max_sessions=None,
        logout_button_label=lambda request: pages.appearance(request).text.log_out,
    )
    for refusal in RefusalPage:
        lobby.add_api_route(
            talk_sign_in_refusal_path(refusal),
            surfaces.talk_sign_in_refusal_page(refusal),
            methods=["GET"],
        )
    add_health_route(lobby)
    lobby.include_router(_session_routes(pages, auth))
    lobby.include_router(preference_routes(pages=pages, voice=private.voice))
    lobby.include_router(source_routes(pages=pages, decks=decks))
    add_deck_pages(lobby, decks=decks, pages=pages, talk_origin=auth.talk_origin)
    lobby.mount(_STATIC_PATH, StaticFiles(directory=_STATIC_DIR), name="static")
    lobby.include_router(fetch_hook(decks=decks))
    if private.admin_people is not None:
        lobby.include_router(
            people_routes(
                admin=private.admin_people,
                # The app is the last segment of the group its people sign in by.
                app=str(auth.admission.login_group).rsplit("/", 1)[-1],
            )
        )
    return lobby
