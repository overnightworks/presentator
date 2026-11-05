"""The fixture the session-access tests share."""

from pathlib import Path

import pytest

from tests.presentator.adapters.support.session_access_support import Arranged
from tests.support.sign_in_support import a_scene


@pytest.fixture
def arranged(tmp_path: Path) -> Arranged:
    """A person signed in over a fake issuer."""
    return Arranged(a_scene(tmp_path))
