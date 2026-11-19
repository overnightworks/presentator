"""Turns an answer's token deltas into whole sentences for the voice."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from presentator.application.preferences import offered_language
from presentator.contracts.copresenter import (
    AnswerText,
    Audio,
    Done,
    Sentence,
    ShowSlide,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from presentator.contracts.copresenter import AnswerEvent, SpeechTokens
    from presentator.contracts.words import Language
    from presentator.ports.copresenter import SentenceBoundaries, Speaking

_TERMINATOR_WITH_CONTENT_AFTER = re.compile(r"[.!?…:;\n]\s*\S")
# The boundary finder judges a word by the words before it; fewer than this
# many made it split differently than on the whole text.
_CONTEXT_WORDS = 8


class SpokenAnswer:
    """Accumulates deltas and releases a sentence only once content follows it.

    The sentence at the buffer's end stays held back because the model may still
    be writing it: `Dr.` at the end is not yet a boundary. Only the buffer's last
    word can still turn into a boundary, so each delta looks at that word, a few
    before it and what follows it; an unterminated stretch costs linear time.
    """

    def __init__(self, boundaries: SentenceBoundaries) -> None:
        """Hold the boundary function that finds sentences."""
        self._boundaries = boundaries
        self._buffer = ""
        self._undecided_from = 0

    def feed(self, delta: str) -> tuple[str, ...]:
        """Add a delta; return the sentences it completed."""
        text = (self._buffer + delta).lstrip()
        start = self._undecided_from
        if _TERMINATOR_WITH_CONTENT_AFTER.search(text, start) is None:
            return self._hold(text, start)
        *completed, _held = self._boundaries(text[start:]) or ("",)
        if not completed:
            return self._hold(text, start)
        end = start
        for sentence in completed:
            end = text.index(sentence, end) + len(sentence)
        first_end = text.index(completed[0], start) + len(completed[0])
        self._hold(text[end:].lstrip(), 0)
        return (text[:first_end].strip(), *completed[1:])

    def flush(self) -> tuple[str, ...]:
        """Return what is left once the model has stopped writing."""
        rest = self._buffer.strip()
        self._buffer = ""
        self._undecided_from = 0
        return (rest,) if rest else ()

    def _hold(self, text: str, searched_from: int) -> tuple[str, ...]:
        self._buffer = text
        self._undecided_from = _last_word_start(text, searched_from)
        return ()


def spoken_language(heard: Language | None, interface: Callable[[], str]) -> Language:
    """The language a question is answered in, written and spoken.

    The language it was heard in, else the offered language the asking person's
    interface language names; that is read only when nothing was heard.
    """
    return heard or offered_language(interface())


async def spoken_events(
    texts: AsyncIterator[str | ShowSlide],
    boundaries: SentenceBoundaries,
    speaking: Speaking,
    language: str,
    tokens: SpeechTokens,
) -> AsyncIterator[AnswerEvent]:
    """Yield the text, each sentence once and in order with its audio, then Done.

    A slide the model shows passes through where it came.
    """
    answer = SpokenAnswer(boundaries)
    spoken: list[str] = []
    async for delta in texts:
        if isinstance(delta, ShowSlide):
            yield delta
            continue
        yield AnswerText(text=delta)
        for sentence in answer.feed(delta):
            spoken.append(sentence)
            async for event in _speak(sentence, speaking, language, tokens):
                yield event
    for sentence in answer.flush():
        spoken.append(sentence)
        async for event in _speak(sentence, speaking, language, tokens):
            yield event
    yield Done(text=" ".join(spoken))


async def _speak(
    sentence: str, speaking: Speaking, language: str, tokens: SpeechTokens
) -> AsyncIterator[AnswerEvent]:
    yield Sentence(text=sentence)
    yield Audio(text=sentence, wav=await speaking.speak(sentence, language, tokens))


def _last_word_start(text: str, not_before: int) -> int:
    index = len(text.rstrip())
    for _ in range(_CONTEXT_WORDS):
        index = _skip_back(text, index, not_before, is_space=False)
        index = _skip_back(text, index, not_before, is_space=True)
    return _skip_back(text, index, not_before, is_space=False)


def _skip_back(text: str, index: int, not_before: int, *, is_space: bool) -> int:
    while index > not_before and text[index - 1].isspace() == is_space:
        index -= 1
    return index
