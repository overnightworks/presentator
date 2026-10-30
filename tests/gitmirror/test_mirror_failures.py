"""What git or its output cannot give is a `MirrorError`, whatever the output holds."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gitmirror.model import MirrorError
from tests.support.git_remote import GitRemote
from tests.support.mirror_support import a_mirror

_PUSHED_AT = datetime(2026, 1, 15, 9, tzinfo=UTC)
# Seconds since the epoch and a committer offset no calendar can hold.
_STAMP_NO_DATE_CAN_HOLD = "1768467600 +9999"


def test_a_missing_path_whose_name_is_not_text_is_a_mirror_error(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit({"talk/slides.md": "# T\n"}, at=_PUSHED_AT)
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None
    not_text = "talk/\udcff.md"

    with pytest.raises(MirrorError, match="�"):
        mirror.read(revision, not_text)


def test_a_commit_date_no_calendar_can_hold_is_a_mirror_error_naming_the_commit(
    remote: GitRemote,
    tmp_path: Path,
) -> None:
    remote.commit({"talk/slides.md": "# T\n"}, at=_PUSHED_AT)
    remote.commit_stamped(
        {"talk/slides.md": "# Later\n"},
        stamp=_STAMP_NO_DATE_CAN_HOLD,
    )
    mirror = a_mirror(remote.url, directory=tmp_path)
    revision = mirror.connect().revision
    assert revision is not None

    with pytest.raises(MirrorError, match=revision.commit):
        mirror.last_change(revision, "talk")
