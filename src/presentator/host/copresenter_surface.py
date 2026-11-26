"""The co-presenter the talk host speaks to: its answers, hearing, speech and ask lease.

Composition only: it names the adapter behind each port, as the composition root does.
"""

from datetime import timedelta
from typing import Final

from webauth.web_auth import WebAuth

from presentator.adapters.postgres.sessions import PostgresSessions
from presentator.adapters.sentences import split_sentences
from presentator.adapters.session_access import SessionAccess
from presentator.adapters.shared_redis import SharedRedis
from presentator.adapters.speech import SpeechHearing, SpeechSpeaking
from presentator.agent.backend import copresenter_gateway, gateway_answerer
from presentator.agent.voice_loop import VoiceLoopTalks
from presentator.api.copresenter import CoPresenterSurface
from presentator.api.health import Stopping
from presentator.application.copresenter import CoPresenterUse
from presentator.application.decks import Decks
from presentator.host.config import Settings
from presentator.ports.copresenter import AskLimiter, DeckTexts

# The wait between taking the lease and the answer's clock starting, so the lease
# outlives the whole answer. It feeds the hold's `lasts`.
ASK_LEASE_MARGIN_SECONDS: Final = 30


def copresenter_surface(  # noqa: PLR0913, PLR0917 - the composition root hands the surface each owner
    settings: Settings,
    sessions: PostgresSessions,
    decks: Decks,
    web_auth: WebAuth,
    texts: DeckTexts,
    redis: SharedRedis,
    stopping: Stopping,
) -> CoPresenterSurface:
    """What only a signed-in admin may reach at the talk host: the gateway's answers."""
    agent = settings.agent.configured(settings.realm.ai_group)
    answers = gateway_answerer(agent, web_auth.relying_party)
    return CoPresenterSurface(
        use=CoPresenterUse(
            answers=answers,
            speech=SpeechHearing(
                settings.speech_service, voices=settings.speech_voices
            ),
            decks=decks.store,
            sources=decks.sources,
            texts=texts,
            boundaries=split_sentences,
            speaking=SpeechSpeaking(
                settings.speech_service, voices=settings.speech_voices
            ),
            talks=VoiceLoopTalks(copresenter_gateway(agent), web_auth.relying_party),
            person_access=SessionAccess(
                web_auth.relying_party,
                sessions,
                web_auth.config,
                speech_scope=settings.realm.speech_scope,
            ),
        ),
        limiter=ask_limiter_of(redis, deadline_seconds=answers.deadline_seconds),
        browser_origin=settings.talk_origin,
        deadline_seconds=answers.deadline_seconds,
        stopping=stopping,
        drain_seconds=settings.talk_drain_seconds,
    )


def ask_limiter_of(redis: SharedRedis, *, deadline_seconds: float) -> AskLimiter:
    """The ask lease: the answer's deadline plus a margin, never renewed."""
    return redis.ask_limiter(
        lasts=timedelta(seconds=deadline_seconds + ASK_LEASE_MARGIN_SECONDS)
    )
