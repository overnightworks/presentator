"""Which folders of a source become deck rows, and which rows a missing folder marks.

The folder name is the deck's address; the manifest and the slides decide whether
a folder is a deck at all. The deck store stays outside.
"""

import logging
from collections import Counter
from datetime import datetime
from typing import Final

from presentator.application import build_retention
from presentator.contracts.decks import (
    SLIDES_FILE,
    Deck,
    DeckFolder,
    Source,
    is_a_plain_folder_name,
)
from presentator.ports.decks import DeckStore

_log = logging.getLogger(__name__)

_TAKE_IN_REFUSED: Final = "take_in.refused"
_NOT_A_FOLDERS_OWN_NAME: Final = "not_a_folders_own_name"
_NAME_HELD_BY_ANOTHER_SOURCE: Final = "name_held_by_another_source"


def take_in(
    store: DeckStore, source: Source, folders: tuple[DeckFolder, ...], at: datetime
) -> None:
    """Store every folder that carries both a manifest and slides.

    The folder name becomes the slug, so a changed title reaches no address.
    A folder whose name is not a folder's own name — such as `..`, planted as a git
    tree entry — is skipped here rather than stored, so it is never a path
    component. A folder the source has stopped carrying is marked as removed, and
    its deck deleted once the folder has been missing for `REMOVED_DECK_KEPT`; a
    folder that comes back under its old name before then loses that mark, so it is
    the deck it was. A folder whose name another source already carries is skipped
    too: the slug is one deck's address here, and the first source keeps it.
    Each reason a folder was skipped for is one `take_in.refused` line carrying how
    many folders it skipped and no name.
    """
    skipped = Counter[str]()
    for folder in folders:
        if not is_a_plain_folder_name(folder.name):
            skipped[_NOT_A_FOLDERS_OWN_NAME] += 1
            continue
        if folder.title is None or SLIDES_FILE not in folder.file_names:
            continue
        taken_in = store.put(
            Deck(
                slug=folder.name,
                title=folder.title,
                changed_at=folder.changed_at,
                owner_id=source.owner_id,
                source_id=source.id,
                commit=folder.commit,
                build=None,
                attempt=None,
            ),
        )
        if not taken_in:
            skipped[_NAME_HELD_BY_ANOTHER_SOURCE] += 1
    for reason, folder_count in skipped.items():
        _log.info(
            _TAKE_IN_REFUSED,
            extra={
                "source_id": source.id,
                "reason": reason,
                "folder_count": folder_count,
            },
        )
    present = frozenset(folder.name for folder in folders)
    build_retention.delete_absent(store, present=present, source_id=source.id, at=at)
