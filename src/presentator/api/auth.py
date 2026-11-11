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
from typing import Final

from agents.tools import not_an_agent_tool
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import RedirectResponse
from starlette.types import Receive, Scope, Send
from webauth.admission import AdmissionConfig
from webauth.config import WebAuthConfig, install_web_auth_config
from webauth.middleware.bearer_gate import (
    BearerGate,
    MachinePaths,
    PersonPaths,
    ProtectedResource,
    install_resource_metadata,
)
from webauth.middleware.session_gate import SessionGate
from webauth.middleware.stack import install_web_auth_middleware
from webauth.oidc import OidcRelyingParty
from webauth.policies import (
    AuthorizedParties,
    BodySizePolicy,
    BodySizeRule,
    CacheControlRule,
    CspDirective,
    CsrfPolicy,
    PathRules,
    PublicRule,
    RateLimitBudget,
    RateLimitPolicy,
    RateLimitRule,
    SecurityHeadersPolicy,
    default_content_security_policy,
)
from webauth.ports import SessionRecordStore, SignInTransaction
from webauth.sign_in_routes import (
    LogoutPageText,
    RefusalPage,
    SignInPaths,
    install_sign_in_routes,
)

from presentator.api.deck_list_api import deck_list_routes
from presentator.api.deck_text_api import deck_text_routes
from presentator.api.decks import add_deck_pages
from presentator.api.health import HEALTH_IS_PUBLIC, HEALTH_PATH, add_health_route
from presentator.api.hooks import HOOK_CALLS, fetch_hook
from presentator.api.lobby_shell import LobbyShell
from presentator.api.people import ADMIN_PREFIX, AdminPeople, people_routes
from presentator.api.preference_api import preference_api_routes
from presentator.api.problems import install_error_answers
from presentator.api.session_api import (
    SESSION_PATH,
    SessionAddresses,
    appearance_of,
    session_routes,
)
from presentator.api.source_api import source_api_routes
from presentator.application.decks import Decks
from presentator.application.own_decks import OwnDecks
from presentator.application.preferences import Preferences
from presentator.contracts.decks import DECK_PATH

_APP_ASSETS: Final = "/_app"
_INSIDE_APP_ASSETS: Final = f"{_APP_ASSETS}/"
_HTML: Final = "text/html"
_PAGE_LOADS: Final = frozenset({HTTPMethod.GET, HTTPMethod.HEAD})
LOGIN_PATH: Final = "/login"
START_SIGN_IN_PATH: Final = "/login/oidc"
_INSIDE_AUTH: Final = "/auth/"
SIGN_IN_CALLBACK_PATH: Final = f"{_INSIDE_AUTH}callback"
BACKCHANNEL_LOGOUT_PATH: Final = f"{_INSIDE_AUTH}backchannel-logout"
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


def sign_in_refusal_path(refusal: RefusalPage) -> str:
    """The lobby's address that shows a refused lobby sign-in."""
    return f"{LOGIN_PATH}/{refusal.value}"


def talk_sign_in_refusal_path(refusal: RefusalPage) -> str:
    """The lobby's address that shows a talk sign-in refusal."""
    return f"{LOGIN_PATH}/talk/{refusal.value}"


_PERSON_API: Final = "/api/"

_REFUSAL_PATHS: Final = frozenset(
    path
    for refusal in RefusalPage
    for path in (sign_in_refusal_path(refusal), talk_sign_in_refusal_path(refusal))
)

# The sign-in addresses are public through the sign-in paths the middleware is
# given; only the back-channel logout among them is exempt from the origin
# check. The lobby app's built assets have to render the open pages themselves,
# so they are public too, and so is the address another server posts to: a
# source host's POST under the hook prefix.
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
                    *_REFUSAL_PATHS,
                }
            )
        )
    ),
    PublicRule(PathRules(prefixes=(_INSIDE_APP_ASSETS,))),
    HEALTH_IS_PUBLIC,
    PublicRule(_SERVER_CALLS, methods=frozenset({HTTPMethod.POST})),
)
# The lobby app's shell answers a page load on any lobby address no route
# claims, but never on these: a missing asset, API or server route stays the
# framework's 404 instead of becoming the app.
_NEVER_THE_SHELL: Final = PathRules(
    exact=frozenset({START_SIGN_IN_PATH, LOGOUT_PATH, HEALTH_PATH, _SETUP}),
    prefixes=(
        _PERSON_API,
        _INSIDE_APP_ASSETS,
        HOOK_CALLS,
        _INSIDE_AUTH,
    ),
)
PERMISSIONS_POLICY: Final = "camera=(), microphone=(), geolocation=()"
_CSRF_POLICY: Final = CsrfPolicy(exempt=_SERVER_CALLS)
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
    ),
)

# The talk host serves a deck's files `no-store`, so one load fetches every file
# of the talk again, a presenter view and an audience window each; its files have
# a budget of their own. The readiness probe is never counted. WebSockets are not
# counted, the co-presenter checks the session on every connection.
TALK_FILES_BUDGET: Final = RateLimitBudget(
    "talk-files", max_requests=3000, window_seconds=60
)
_NOT_COUNTED: Final = PathRules(exact=frozenset({HEALTH_PATH}))
LOBBY_RATE_LIMIT: Final = RateLimitPolicy(exempt=_NOT_COUNTED)
TALK_RATE_LIMIT: Final = RateLimitPolicy(
    # With the slash, so `/deckX` is not a deck file.
    rules=(RateLimitRule(PathRules(prefixes=(f"{DECK_PATH}/",)), TALK_FILES_BUDGET),),
    exempt=_NOT_COUNTED,
)
LOGIN_REDIRECT: Final = LOBBY_SIGN_IN.login_redirect


# SvelteKit writes its accessibility announcer with this one inline style
# attribute, which `style-src 'self'` refuses; the policy admits exactly it by
# its hash under 'unsafe-hashes', since hiding it by a build step would patch
# the framework. It is the sha256 of the attribute's value in @sveltejs/kit
# 3.1.0's runtime/components/root.svelte, so a kit update that changes the
# style turns the browser drive red. It goes when SvelteKit writes the
# announcer without an inline style (sveltejs/kit#11747).
_ANNOUNCER_STYLE_HASH: Final = "sha256-S8qMpvofolR8Mpjy4kQvEm7m1q8clzU4dfDH0AmvZjo="


def content_security_policy(shell: LobbyShell) -> str:
    """The one policy every answer of the lobby carries, except the logout page.

    Nothing in the lobby needs an external host or a frame: every asset ships
    under `/_app`, served from this origin. The one inline script
    is the app's bootstrap, admitted by its hash, and the one inline style is
    SvelteKit's announcer. The logout page brings its own policy, which
    webauth's security headers leave in place.
    """
    return default_content_security_policy(
        [shell.bootstrap_hash],
        added_sources={
            CspDirective.STYLE_SRC: ["'unsafe-hashes'", f"'{_ANNOUNCER_STYLE_HASH}'"]
        },
    )


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
    # The realm that signs people in, which also serves their central account.
    issuer: str

    @property
    def talk_sign_in_address(self) -> str:
        """Where a sign-in starts at the talk host."""
        return f"{self.talk_origin}{START_SIGN_IN_PATH}"

    @property
    def account_console_url(self) -> str:
        """Where the person manages their central account."""
        return f"{self.issuer}/account"


@dataclass(frozen=True, slots=True, kw_only=True)
class LobbyPrivate:
    """Private capabilities the authenticated lobby may expose to an admin."""

    # Absent: no token client is named, so the admin people route does not exist.
    admin_people: AdminPeople | None = None


type ResourceInstaller = Callable[[FastAPI], ProtectedResource]


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentFront:
    """What the host hands the lobby for the agent of a signed-in person.

    The installer runs once every route that carries a schema is registered and
    answers the resource the bearer gate admits tokens for.
    """

    install_resource: ResourceInstaller


@dataclass(frozen=True, slots=True, kw_only=True)
class _Surfaces:
    """The lobby's HTML answers, each one asking the use cases what is true."""

    preferences: Preferences
    auth: InstalledAuth
    shell: LobbyShell

    def lobby_app(
        self, request: Request, *, status: HTTPStatus = HTTPStatus.OK
    ) -> Response:
        """The lobby app's shell, in the reader's language and theme."""
        return HTMLResponse(
            self.shell.filled(appearance_of(request, self.preferences)),
            status_code=status,
        )

    def not_found_page(self, request: Request) -> Response:
        """The app's shell under 404; the router shows the not-found board."""
        return self.lobby_app(request, status=HTTPStatus.NOT_FOUND)

    def refusal_page(self, request: Request) -> Response:
        """The app's shell under 403; the router shows why from the address."""
        return self.lobby_app(request, status=HTTPStatus.FORBIDDEN)


def _loads_the_lobby_app(request: Request) -> bool:
    """A browser's page load on an address the app's router decides."""
    return (
        request.method in _PAGE_LOADS
        and _HTML in request.headers.get("accept", "")
        and not _NEVER_THE_SHELL.matches(request.url.path)
    )


def _install_lobby_app(lobby: FastAPI, surfaces: _Surfaces) -> None:
    """Answer a page load no route claims with the app's shell.

    The app's built assets are served under `/_app`. The shell takes the
    router's not-found place rather than a catch-all route, so a route's own
    404 and a wrong method's 405 stay the framework's, and everything that is
    no page load still gets the framework's 404.
    """
    lobby.mount(
        _APP_ASSETS, StaticFiles(directory=surfaces.shell.assets), name="lobby_app"
    )
    not_found = lobby.router.default

    async def lobby_app_or_not_found(
        scope: Scope, receive: Receive, send: Send
    ) -> None:
        if scope["type"] == "http":
            request = Request(scope, receive)
            if _loads_the_lobby_app(request):
                answer = await run_in_threadpool(surfaces.lobby_app, request)
                await answer(scope, receive, send)
                return
        await not_found(scope, receive, send)

    lobby.router.default = lobby_app_or_not_found


def _logout_page_text(request: Request, surfaces: _Surfaces) -> LogoutPageText:
    text = appearance_of(request, surfaces.preferences).text
    return LogoutPageText(language=text.language_tag, button_label=text.log_out)


def _install_sign_in(lobby: FastAPI, surfaces: _Surfaces) -> None:
    """The library's sign-in routes, and the 403 shell at every refusal address."""
    auth = surfaces.auth
    install_sign_in_routes(
        lobby,
        paths=LOBBY_SIGN_IN,
        relying_party=lambda _request: auth.relying_party,
        admission=auth.admission,
        transaction=auth.transaction,
        refused=lambda _request, refusal: RedirectResponse(
            sign_in_refusal_path(refusal.page), status_code=HTTPStatus.SEE_OTHER
        ),
        keeps_id_token=True,
        max_sessions=None,
        ui_language=lambda _request: None,
        logout_page_text=lambda request: _logout_page_text(request, surfaces),
    )
    for path in _REFUSAL_PATHS:
        lobby.add_api_route(
            path,
            surfaces.refusal_page,
            methods=["GET"],
            openapi_extra=not_an_agent_tool(reason="An HTML page."),
        )


def _install_middleware(
    lobby: FastAPI,
    auth: InstalledAuth,
    sessions: SessionRecordStore,
    admin: AdminPeople | None,
    resource: ProtectedResource,
) -> None:
    """Install the library's stack once, in its own order.

    Every answer, including the gate's redirect and a refusal, carries the
    security headers. The gate's store calls commit on their own and `touch` is
    one update-only statement, so the gate's admission takes no lock.
    The admin prefix, when it is served, takes a bearer token and no cookie.
    While the admin prefix is served, `/api/` takes a person's token as that
    person; a request without a token is decided by the session cookie. The
    resource takes the agents client's token at `/mcp` and on
    the routes marked as tools; its discovery document is public.
    """
    install_error_answers(lobby)
    install_web_auth_middleware(
        lobby,
        sign_in_paths=LOBBY_SIGN_IN,
        security_headers=SecurityHeadersPolicy(
            content_security_policy=auth.content_security_policy,
            permissions_policy=PERMISSIONS_POLICY,
            cache_control_rules=(
                CacheControlRule(PathRules(prefixes=("/",)), "no-store"),
            ),
        ),
        body_size=_BODY_SIZE,
        rate_limit=LOBBY_RATE_LIMIT,
        bearer_gate=_bearer_gate(auth, admin, resource),
        csrf_origin=_CSRF_POLICY,
        session_gate=SessionGate(
            session_transaction=lambda: nullcontext(sessions),
            login_redirect=LOGIN_REDIRECT,
            public=(*_PUBLIC_RULES, resource.public_rule),
            optional=PathRules(exact=frozenset({SESSION_PATH})),
        ),
    )


def _bearer_gate(
    auth: InstalledAuth, admin: AdminPeople | None, resource: ProtectedResource
) -> BearerGate:
    """The gate: the resource's token always, the people's only with token clients."""
    if admin is None:
        return BearerGate(
            relying_party=auth.relying_party,
            admission=auth.admission,
            resource=resource,
        )
    parties = AuthorizedParties(admin.admitted_clients)
    return BearerGate(
        relying_party=auth.relying_party,
        admission=auth.admission,
        machine_paths=MachinePaths(PathRules(prefixes=(ADMIN_PREFIX,)), parties),
        person_paths=PersonPaths(
            PathRules(prefixes=(_PERSON_API,)), parties, auth.transaction
        ),
        resource=resource,
    )


def _session_routes(preferences: Preferences, auth: InstalledAuth) -> APIRouter:
    return session_routes(
        preferences=preferences,
        addresses=SessionAddresses(
            start=START_SIGN_IN_PATH,
            logout=LOGOUT_PATH,
            talk_start=auth.talk_sign_in_address,
            account_console_url=auth.account_console_url,
        ),
    )


def _install_resource(
    lobby: FastAPI, agent: AgentFront, issuer: str
) -> ProtectedResource:
    """The resource and its public discovery document, once every route is in."""
    resource = agent.install_resource(lobby)
    install_resource_metadata(lobby, resource, issuer=issuer)
    return resource


def create_lobby(  # noqa: PLR0913 - the composition root's one call names each owner
    *,
    sessions: SessionRecordStore,
    decks: Decks,
    preferences: Preferences,
    auth: InstalledAuth,
    private: LobbyPrivate,
    agent: AgentFront,
    own_decks: OwnDecks,
    shell: LobbyShell,
) -> FastAPI:
    """Build the lobby around the use cases and the adapters the host chose.

    The fetch hook always stands under `/sources/`; a call that cannot name a
    stored source and its secret is answered alike, and GET stays behind the
    session. The app's shell answers what no route claims, and the refusal
    addresses under 403.
    """
    surfaces = _Surfaces(preferences=preferences, auth=auth, shell=shell)
    lobby = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_web_auth_config(lobby, auth.config)
    _install_sign_in(lobby, surfaces)
    add_health_route(lobby)
    lobby.include_router(_session_routes(preferences, auth))
    lobby.include_router(deck_list_routes(decks=decks))
    lobby.include_router(deck_text_routes(own_decks=own_decks))
    lobby.include_router(preference_api_routes(preferences=preferences))
    lobby.include_router(source_api_routes(decks=decks))
    add_deck_pages(
        lobby,
        decks=decks,
        talk_origin=auth.talk_origin,
        not_found=surfaces.not_found_page,
    )
    lobby.include_router(fetch_hook(decks=decks))
    if private.admin_people is not None:
        lobby.include_router(
            people_routes(
                admin=private.admin_people,
                # The app is the last segment of the group its people sign in by.
                app=str(auth.admission.login_group).rsplit("/", 1)[-1],
            )
        )
    resource = _install_resource(lobby, agent, auth.issuer)
    _install_middleware(lobby, auth, sessions, private.admin_people, resource)
    _install_lobby_app(lobby, surfaces)
    return lobby
