"""A source's folder: how a typed one is kept, refused, and where decks stand."""

import pytest

from presentator.contracts.decks import (
    MAXIMUM_NAME_BYTES,
    Source,
    is_a_folder_path,
    normalized_folder,
)

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
_KEPT = [
    "",
    "examples",
    "talks/2026",
    "a" * MAXIMUM_NAME_BYTES,
    _FITS_IN_BYTES,
    f"a/{_FITS_IN_BYTES}",
]


# SR8
@pytest.mark.parametrize("folder", _REFUSALS)
def test_a_malformed_folder_is_not_a_folder_path(folder: str) -> None:
    assert not is_a_folder_path(normalized_folder(folder))


@pytest.mark.parametrize("folder", _KEPT)
def test_a_well_formed_folder_is_a_folder_path(folder: str) -> None:
    assert is_a_folder_path(normalized_folder(folder))


# SR8
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
    nested = _source(folder="examples/talks")
    top = _source()

    assert nested.deck_path("intro") == "examples/talks/intro"
    assert top.deck_path("intro") == "intro"


def _source(folder: str = "") -> Source:
    return Source(
        id="s",
        name="decks",
        url="https://example.invalid/r.git",
        ref="main",
        owner_id="o",
        folder=folder,
    )
