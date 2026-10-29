"""The catalogs the lobby speaks, and what happens when one is incomplete."""

from dataclasses import fields
from pathlib import Path

import pytest

from presentator.adapters.catalog import (
    CATALOG_DIRECTORY,
    IncompleteCatalogError,
    load_catalogs,
    load_lobby_text,
)
from presentator.contracts.text import DEFAULT_LANGUAGE_TAG, Language, LobbyText

_HEADER = 'msgid ""\nmsgstr "Language: en\\n"\n'
_SHIPPED_CATALOG = CATALOG_DIRECTORY / f"{DEFAULT_LANGUAGE_TAG}.po"


def a_catalog_file(directory: Path, tag: str, language_name: str) -> None:
    """A complete catalog: every message the lobby needs, under one tag."""
    messages = "".join(
        f'\nmsgid "{field.name}"\nmsgstr "{field.name} in {tag}"\n'
        for field in fields(LobbyText)
        if field.name not in {"language_tag", "language_name"}
    )
    (directory / f"{tag}.po").write_text(
        f'msgid ""\nmsgstr "Language: {tag}\\n"\n'
        f'\nmsgid "language_name"\nmsgstr "{language_name}"\n{messages}',
        encoding="utf-8",
    )


def test_the_shipped_catalog_carries_every_word_the_lobby_needs() -> None:
    text = load_lobby_text(_SHIPPED_CATALOG)

    assert text.language_tag == DEFAULT_LANGUAGE_TAG


def test_a_catalog_file_dropped_into_the_directory_becomes_a_choice(
    tmp_path: Path,
) -> None:
    a_catalog_file(tmp_path, "en", "English")
    a_catalog_file(tmp_path, "de", "Deutsch")

    catalogs = load_catalogs(tmp_path)

    assert catalogs.languages() == (
        Language(tag="de", name="Deutsch"),
        Language(tag="en", name="English"),
    )
    assert catalogs.text("de").wordmark == "wordmark in de"


def test_a_catalog_that_names_no_language_is_refused(tmp_path: Path) -> None:
    catalog = tmp_path / "nowhere.po"
    catalog.write_text('msgid "wordmark"\nmsgstr "Presentator"\n', encoding="utf-8")

    with pytest.raises(IncompleteCatalogError, match="names no language"):
        load_lobby_text(catalog)


def test_a_catalog_that_misses_a_word_is_refused(tmp_path: Path) -> None:
    catalog = tmp_path / "half.po"
    catalog.write_text(
        f'{_HEADER}\nmsgid "wordmark"\nmsgstr "Presentator"\n',
        encoding="utf-8",
    )

    with pytest.raises(IncompleteCatalogError, match="no message for language_name"):
        load_lobby_text(catalog)
