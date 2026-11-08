"""A source's folder: how a typed one is kept, refused, and where decks stand."""

import pytest

from presentator.contracts.decks import (
    MAXIMUM_NAME_BYTES,
    is_a_folder_path,
    normalized_folder,
)
from tests.support.deck_source_support import a_source

_FITS_IN_BYTES = "é" * (MAXIMUM_NAME_BYTES // 2)
_ONE_BYTE_OVER = "é" * (MAXIMUM_NAME_BYTES // 2 + 1)
_REFUSALS = [
    "/",
    "//",
    "..",
    "/x",
    "a//b",
    ".",
    "a/../b",
    "a\\b",
    'a"b',
    "a\nb",
    _ONE_BYTE_OVER,
    f"examples/{_ONE_BYTE_OVER}",
]
_KEPT = ["", "examples", "talks/2026", _FITS_IN_BYTES, f"a/{_FITS_IN_BYTES}"]


@pytest.mark.parametrize("folder", _REFUSALS)
def test_a_malformed_folder_is_not_a_folder_path(folder: str) -> None:
    assert not is_a_folder_path(normalized_folder(folder))


@pytest.mark.parametrize("folder", _KEPT)
def test_a_well_formed_folder_is_a_folder_path(folder: str) -> None:
    assert is_a_folder_path(normalized_folder(folder))


@pytest.mark.parametrize(
    ("typed", "kept"),
    [
        ("examples/", "examples"),
        (" examples ", "examples"),
        ("  talks/2026/ ", "talks/2026"),
        ("/", "/"),
        ("", ""),
    ],
)
def test_a_typed_folder_loses_its_surrounding_space_and_one_trailing_slash(
    typed: str, kept: str
) -> None:
    assert normalized_folder(typed) == kept


def test_a_decks_path_stands_under_its_sources_folder() -> None:
    nested = a_source("https://example.invalid/r.git", folder="examples/talks")
    top = a_source("https://example.invalid/r.git")

    assert nested.deck_path("intro") == "examples/talks/intro"
    assert top.deck_path("intro") == "intro"
