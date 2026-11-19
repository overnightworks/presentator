"""How a stored word reads when a newer release wrote one this release does not know.

A rollback, or a rollout with two releases serving at once, leaves rows a newer
release wrote. Every persisted word a listing or a refresh reads goes through
`read_persisted`, so such a row reads as the generic case its caller names and
the page keeps working, instead of one row breaking every listing until a newer
release rewrites it. Words that grant rights are not read this way: an unknown
role or account kind still fails loud.
"""

import logging
from enum import StrEnum
from functools import cache
from typing import Final

_UNKNOWN_WORD_EVENT: Final = "persisted_word.unknown_read"

_log = logging.getLogger(__name__)


def read_persisted[Member: StrEnum, Generic](
    words: type[Member], stored: str, *, unknown: Generic
) -> Member | Generic:
    """The member `stored` names, or `unknown` for a word a newer release wrote."""
    try:
        return words(stored)
    except ValueError:
        _tell_once(words.__name__, stored)
        return unknown


@cache
def _tell_once(words: str, stored: str) -> None:
    """Log each unknown word once per process; every listing would repeat it."""
    _log.warning(_UNKNOWN_WORD_EVENT, extra={"words": words, "stored": stored})
