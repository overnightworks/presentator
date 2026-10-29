"""E11 (#355): the co-presenter answers only about a built deck its asker owns."""

from __future__ import annotations

from dataclasses import replace
from http import HTTPStatus
from typing import TYPE_CHECKING

import pytest
from webauth.cookies import DEFAULT_SESSION_COOKIE_NAME

from presentator.application.copresenter import AskLimiter, AskLimits
from presentator.contracts.copresenter import DeckText
from presentator.contracts.models import Role
from tests.presentator.application.support.fakes import FrozenClock
from tests.support.copresenter_support import (
    ASKED,
    NOW,
    QUESTION,
    a_copresenter_talk_host,
    ask,
    the_admins_decks,
    who,
)

if TYPE_CHECKING:
    from httpx2 import Response

    from presentator.contracts.decks import Deck
    from tests.support.copresenter_support import PrivateFakes
    from tests.support.lobby import Lobby

TARGET = "target"
ONE_QUESTION_ONLY = AskLimits(per_window=1)
BUILT_TARGET = replace(ASKED.deck, slug=TARGET)
UNBUILT = replace(BUILT_TARGET, build=None)
ANOTHERS = replace(BUILT_TARGET, owner_id="id-of-someone-else")
SOURCELESS = replace(BUILT_TARGET, source_id="a-source-long-gone")


def ask_about(lobby: Lobby, slug: str) -> Response:
    return ask(lobby.client, {**QUESTION, "deck": slug})


def an_instance(
    *others: Deck, removed: tuple[str, ...] = (), role: Role = Role.ADMIN
) -> tuple[Lobby, PrivateFakes]:
    """A person signed in, a budget of one question, and the admin's own deck."""
    decks = the_admins_decks()
    for deck in others:
        decks.put(deck)
        if deck.build is not None:
            decks.put_build(deck.slug, deck.build)
    decks.removed.update(removed)
    lobby, private, _clock = a_copresenter_talk_host(
        decks=decks,
        limiter=AskLimiter(clock=FrozenClock(instant=NOW), limits=ONE_QUESTION_ONLY),
    )
    lobby.sign_in(role=role)
    return lobby, private


def the_answer_to_a_deck_that_does_not_exist() -> tuple[bytes, dict[str, str]]:
    lobby, _private = an_instance()
    answer = ask_about(lobby, "ghost")
    assert answer.status_code == HTTPStatus.NOT_FOUND
    return answer.content, dict(answer.headers)


def test_the_owner_asks_about_their_own_built_deck() -> None:
    """E11 (#355) matrix, deck: the owner's own built deck is answered."""
    lobby, private = an_instance()

    answer = ask(lobby.client)

    assert answer.status_code == HTTPStatus.OK
    assert private.texts.asked == [(ASKED.deck, ASKED.source)]


@pytest.mark.parametrize(
    ("others", "removed", "slug"),
    [
        pytest.param((UNBUILT,), (), TARGET, id="own and unbuilt"),
        pytest.param((BUILT_TARGET,), (TARGET,), TARGET, id="removed"),
        pytest.param((ANOTHERS,), (), TARGET, id="another admin's"),
        pytest.param((), (), TARGET, id="nonexistent"),
        pytest.param((SOURCELESS,), (), TARGET, id="source gone"),
        pytest.param((), (), "..", id="dot dot"),
        pytest.param((), (), "a/b", id="path with a slash"),
        pytest.param((), (), "a\x00b", id="control character"),
        pytest.param((), (), "x" * 255, id="at the length bound"),
    ],
)
def test_a_refused_deck_gets_one_answer_and_spends_no_question(
    others: tuple[Deck, ...], removed: tuple[str, ...], slug: str
) -> None:
    """E11 (#355) matrix, deck: every unusable deck gets one 404, no port, no budget."""
    lobby, private = an_instance(*others, removed=removed)

    refused = ask_about(lobby, slug)

    assert refused.status_code == HTTPStatus.NOT_FOUND
    ghost_content, ghost_headers = the_answer_to_a_deck_that_does_not_exist()
    assert refused.content == ghost_content
    assert dict(refused.headers) == ghost_headers
    assert private.answers.messages == []
    assert ask(lobby.client).status_code == HTTPStatus.OK


def test_a_question_without_a_deck_gets_the_same_refusal() -> None:
    """E11 (#355) matrix, deck: a missing deck field gets the one 404."""
    lobby, private = an_instance()
    without_deck = {key: value for key, value in QUESTION.items() if key != "deck"}

    refused = ask(lobby.client, without_deck)

    assert refused.status_code == HTTPStatus.NOT_FOUND
    ghost_content, ghost_headers = the_answer_to_a_deck_that_does_not_exist()
    assert refused.content == ghost_content
    assert dict(refused.headers) == ghost_headers
    assert private.answers.messages == []
    assert ask(lobby.client).status_code == HTTPStatus.OK


def test_a_name_over_the_length_bound_is_rejected_before_any_lookup() -> None:
    """E11 (#355) matrix, deck: a name over 255 is 422 and spends no question."""
    lobby, private = an_instance()

    refused = ask_about(lobby, "x" * 256)

    assert refused.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert private.answers.messages == []
    assert ask(lobby.client).status_code == HTTPStatus.OK


def test_an_owner_with_the_user_role_is_answered_and_another_users_deck_is_not() -> (
    None
):
    """E11 (#355), amended 27.10.2026: ownership decides, not the role."""
    lobby, private = an_instance(ANOTHERS, role=Role.USER)

    refused = ask_about(lobby, TARGET)

    assert refused.status_code == HTTPStatus.NOT_FOUND
    assert private.answers.messages == []
    assert ask(lobby.client).status_code == HTTPStatus.OK
    assert len(private.answers.messages) == 1


def test_a_signed_out_asker_is_refused_and_spends_no_question() -> None:
    """E11 (#355) matrix, asker: signed out is refused 401, no port, no budget."""
    lobby, private, _clock = a_copresenter_talk_host(
        limiter=AskLimiter(clock=FrozenClock(instant=NOW), limits=ONE_QUESTION_ONLY),
    )

    lobby.sign_in(role=Role.ADMIN)
    session = lobby.client.cookies[DEFAULT_SESSION_COOKIE_NAME]
    lobby.client.cookies.delete(DEFAULT_SESSION_COOKIE_NAME)

    refused = lobby.client.post(
        "/copresenter/ask", json=QUESTION, headers={"accept": "*/*"}
    )
    lobby.client.cookies.set(DEFAULT_SESSION_COOKIE_NAME, session)

    assert refused.status_code == HTTPStatus.UNAUTHORIZED
    assert private.answers.messages == []
    assert ask(lobby.client).status_code == HTTPStatus.OK


def a_text_in(language: str | None) -> DeckText:
    return DeckText(title="t", language=language, slides=())


def who_refusals_match_the_asks_refusal(answer: Response) -> bool:
    ghost_content, ghost_headers = the_answer_to_a_deck_that_does_not_exist()
    return (
        answer.status_code == HTTPStatus.NOT_FOUND
        and answer.content == ghost_content
        and dict(answer.headers) == ghost_headers
    )


@pytest.mark.parametrize(
    ("others", "removed", "deck"),
    [
        pytest.param((), (), None, id="absent"),
        pytest.param((), (), "", id="empty"),
        pytest.param((), (), "..", id="dot dot"),
        pytest.param((), (), "a/b", id="path with a slash"),
        pytest.param((), (), TARGET, id="nonexistent"),
        pytest.param((UNBUILT,), (), TARGET, id="own and unbuilt"),
        pytest.param((SOURCELESS,), (), TARGET, id="source gone"),
        pytest.param((BUILT_TARGET,), (TARGET,), TARGET, id="removed"),
        pytest.param((ANOTHERS,), (), TARGET, id="another person's"),
    ],
)
def test_who_refuses_a_deck_the_asker_does_not_own_built_like_ask_does(
    others: tuple[Deck, ...], removed: tuple[str, ...], deck: str | None
) -> None:
    """#615 cells 3-6: one 404, no deck read, no readiness call."""
    lobby, private = an_instance(*others, removed=removed)

    refused = who(lobby.client, deck)

    assert who_refusals_match_the_asks_refusal(refused)
    assert private.texts.asked == []
    assert private.speech.readiness_calls == 0


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("role", [Role.ADMIN, Role.USER])
def test_who_answers_the_decks_language_to_its_owner(language: str, role: Role) -> None:
    """#615 cells 7, 8, 14: de or en as written, for every signed-in owner."""
    lobby, private = an_instance(role=role)
    private.texts.text = a_text_in(language)

    answer = who(lobby.client)

    assert answer.status_code == HTTPStatus.OK
    assert answer.json()["language"] == language


@pytest.mark.parametrize("written", [None, "fr", "EN", "de-DE"])
def test_who_answers_german_for_a_language_it_does_not_know(
    written: str | None,
) -> None:
    """#615 cell 9: no key, a manifest-less deck or another value is German."""
    lobby, private = an_instance()
    private.texts.text = a_text_in(written)

    assert who(lobby.client).json()["language"] == "de"


@pytest.mark.parametrize("failure", ["mirror", "no slides"])
def test_who_is_unavailable_when_the_decks_words_cannot_be_read(failure: str) -> None:
    """#615 cells 10, 11: never a German success that was not read."""
    lobby, private = an_instance()
    private.texts.unreadable = failure == "mirror"
    private.texts.text = None

    answer = who(lobby.client)

    assert answer.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert answer.json() == {"detail": "Co-presenter unavailable"}
    assert private.speech.readiness_calls == 0


def test_who_is_unavailable_when_speech_is_unavailable() -> None:
    """#615 cell 12: speech readiness still decides the 503."""
    lobby, private = an_instance()
    private.texts.text = a_text_in("en")
    private.speech.readiness_unavailable = True

    assert who(lobby.client).status_code == HTTPStatus.SERVICE_UNAVAILABLE


@pytest.mark.parametrize("slug", ["50%off", "a&b c+d", "ü"])
def test_who_names_the_owned_deck_whose_slug_needs_percent_encoding(slug: str) -> None:
    """#615 cell 15: the slug reaches the server as the slug."""
    lobby, private = an_instance(replace(BUILT_TARGET, slug=slug))
    private.texts.text = a_text_in("en")

    answer = who(lobby.client, slug)

    assert answer.status_code == HTTPStatus.OK
    assert [deck.slug for deck, _source in private.texts.asked] == [slug]


def test_who_counts_no_question() -> None:
    """#615 cell 16: after who, the one question of the window is still admitted."""
    lobby, _private = an_instance()

    assert who(lobby.client).status_code == HTTPStatus.OK
    assert ask(lobby.client).status_code == HTTPStatus.OK
