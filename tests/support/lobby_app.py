"""The lobby and talk apps a test drives, over the fakes of `tests.support.lobby`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import ASGIApp
from webauth.config import installed_web_auth_config
from webauth.proxies import TrustedProxies
from webauth.web_auth import web_auth_front

from presentator.api.auth import AgentFront, LobbyPrivate, create_lobby
from presentator.api.copresenter import CoPresenterSurface
from presentator.api.lobby_shell import LobbyShell
from presentator.api.people import AdminPeople
from presentator.api.talk import create_talk
from presentator.application.own_decks import OwnDecks
from presentator.application.preferences import Preferences
from presentator.host.main import agent_front
from presentator.ports.copresenter import DeckTexts
from presentator.ports.preferences import PersonPreferencesStore
from tests.presentator.application.support.fakes import (
    FakePeople,
    FakeUserStore,
    FrozenClock,
)
from tests.support.deck_text_support import FakeDeckTexts
from tests.support.lobby import (
    A_BROWSERS_ADDRESS,
    A_BROWSERS_HEADERS,
    A_REALM,
    A_TALK_ADDRESS,
    AN_ISSUER,
    LOBBY_SHELL,
    NO_DECKS,
    GivenDecks,
    Lobby,
    SessionsOnly,
    SigningIn,
    a_crowd,
    a_decks,
    a_lobby_auth,
    a_preferences,
    a_talk_auth,
)


def an_agent_front() -> AgentFront:
    """The agent's front as the host composes it, at the test origin."""
    return agent_front(
        origin=A_BROWSERS_ADDRESS,
        issuer=AN_ISSUER,
        trusted_proxies=TrustedProxies.parse(""),
        realm=A_REALM,
    )


def behind_the_front(app: FastAPI, *, trusted_proxies: str = "") -> ASGIApp:
    """`app` served the way the host serves it: behind webauth's front.

    An app built for the shared front fails its first request served bare, so
    a test that drives one alone puts it behind its own front; a host router
    takes the bare app and puts its own front around both.
    """
    return web_auth_front(app, trusted_proxies=TrustedProxies.parse(trusted_proxies))


def a_lobby_app(  # noqa: PLR0913 - each part is the seam one test replaces
    *,
    people: FakePeople | None = None,
    preferences: Preferences | None = None,
    given: GivenDecks = NO_DECKS,
    signing_in: SigningIn | None = None,
    admin_people: AdminPeople | None = None,
    agent: AgentFront | None = None,
    texts: DeckTexts | None = None,
    shell: LobbyShell = LOBBY_SHELL,
) -> tuple[FastAPI, FrozenClock]:
    """The whole lobby wiring, so a test that needs two clients shares one app.

    The app is built for the shared front, as the host builds it: a browser
    reaches it through `behind_the_front`.
    """
    crowd = a_crowd() if people is None else people
    clock = crowd.clock
    decks = (
        a_decks(given, clock)
        if signing_in is None or signing_in.decks is None
        else signing_in.decks
    )
    sessions = crowd.sessions if signing_in is None else signing_in.sessions
    kept = a_preferences() if preferences is None else preferences
    web_auth = a_lobby_auth(
        clock,
        sessions=sessions,
        preferences=kept,
        transaction=SessionsOnly(crowd.sessions)
        if signing_in is None
        else signing_in.transaction,
        issuer_calls=None if signing_in is None else signing_in.issuer_calls,
        client=None if signing_in is None else signing_in.client,
        config=None if signing_in is None else signing_in.config,
        shell=shell,
    )
    lobby = create_lobby(
        sessions=sessions,
        decks=decks,
        preferences=kept,
        web_auth=web_auth,
        talk_origin=A_TALK_ADDRESS,
        private=LobbyPrivate(
            admin_people=admin_people,
        ),
        agent=an_agent_front() if agent is None else agent,
        own_decks=OwnDecks(
            decks=decks.store,
            sources=decks.sources,
            texts=FakeDeckTexts(text=None) if texts is None else texts,
        ),
        shell=shell,
    )
    return lobby, clock


def a_talk_app(
    *,
    people: FakePeople,
    copresenter: CoPresenterSurface | None = None,
) -> FastAPI:
    """The whole talk host over the lobby's people, with the co-presenter it serves."""
    clock = people.clock
    web_auth = a_talk_auth(
        clock,
        sessions=people.sessions,
        transaction=SessionsOnly(people.sessions),
    )
    return create_talk(
        sessions=people.sessions,
        decks=a_decks(NO_DECKS, clock),
        web_auth=web_auth,
        lobby_origin=A_BROWSERS_ADDRESS,
        copresenter=copresenter,
    )


def a_browser_at(
    app: FastAPI,
    *,
    people: FakePeople,
    base_url: str,
    headers: dict[str, str],
    trusted_proxies: str = "",
) -> Lobby:
    """A browser at an app built over `people`, with nobody signed in yet.

    The browser reaches the app through its front, which believes the
    `trusted_proxies` network.
    """
    served = behind_the_front(app, trusted_proxies=trusted_proxies)
    return Lobby(
        app=app,
        served=served,
        client=TestClient(
            served,
            base_url=base_url,
            follow_redirects=False,
            headers=headers,
        ),
        clock=people.clock,
        people=people,
        config=installed_web_auth_config(app),
    )


def a_lobby(
    *,
    users: FakeUserStore | None = None,
    given: GivenDecks = NO_DECKS,
    person_preferences: PersonPreferencesStore | None = None,
) -> Lobby:
    """The whole lobby, with in-memory stores behind every port."""
    people = a_crowd(users)
    app, _ = a_lobby_app(
        people=people,
        preferences=a_preferences(people=person_preferences),
        given=given,
    )
    return a_browser_at(
        app,
        people=people,
        base_url=A_BROWSERS_ADDRESS,
        headers=A_BROWSERS_HEADERS,
    )


def a_lobby_that_claims_no_origin_of_its_own() -> Lobby:
    """A browser old enough to omit Sec-Fetch-Site, for a test that names its own.

    `a_lobby`'s client already claims `Sec-Fetch-Site: same-origin` so most
    tests never have to; a test proving what an absent or a foreign claim
    does needs a client that makes no claim of its own to be overridden.
    """
    people = a_crowd()
    app, _ = a_lobby_app(people=people)
    return a_browser_at(
        app,
        people=people,
        base_url=A_BROWSERS_ADDRESS,
        headers={"accept": "text/html"},
    )


def a_signed_in_lobby(given: GivenDecks = NO_DECKS) -> TestClient:
    """The same lobby with its admin's session open."""
    lobby = a_lobby(given=given)
    lobby.sign_in()
    return lobby.client
