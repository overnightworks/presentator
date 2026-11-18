"""A deck's words, read from the run its talk was built in."""

import time
from dataclasses import replace
from pathlib import Path

import pytest

from presentator.adapters.build_cache import BuildCache
from presentator.adapters.deck_texts import BuiltDeckTexts
from presentator.contracts.copresenter import CoPresenterUnavailableError, DeckText
from presentator.contracts.decks import Build, Deck
from tests.support.deck_source_support import a_build, a_deck, a_source
from tests.support.run_objects import A_CACHE_BOUND, DirectoryRunObjects

_TALK = "talk"
_QUICK_SECONDS = 5
_SLIDES = "# One\nfirst\n\n---\n\n# Two\nsecond\n\n<!-- notes for two -->\n"


def built(deck: Deck, build: Build) -> Deck:
    return replace(deck, build=build)


def texts_under(tmp_path: Path, *, answering: bool = True) -> BuiltDeckTexts:
    return BuiltDeckTexts(
        cache=BuildCache(
            root=tmp_path / "builds",
            store=DirectoryRunObjects(tmp_path / "store", answering=answering),
            bound_bytes=A_CACHE_BOUND,
        )
    )


def lay_down(tmp_path: Path, files: dict[str, str]) -> None:
    """The deck's tree as the build of `a_build` left it in its run."""
    deck = tmp_path / "builds" / a_build(tmp_path).run.name / "deck"
    deck.mkdir(parents=True)
    (deck.parent / "out").mkdir()
    for name, text in files.items():
        (deck / name).write_text(text, encoding="utf-8")


def read_deck(tmp_path: Path, slug: str) -> DeckText | None:
    deck = built(a_deck(slug), a_build(tmp_path))
    return texts_under(tmp_path).text_of(deck, a_source("git@heimserver:decks.git"))


def test_a_built_deck_gives_its_title_language_and_slides_with_notes(
    tmp_path: Path,
) -> None:
    lay_down(
        tmp_path,
        {
            "slides.md": _SLIDES,
            "deck.toml": 'title = "The Talk"\nlanguage = "en"\n',
        },
    )

    text = read_deck(tmp_path, _TALK)

    assert text is not None
    assert (text.title, text.language) == ("The Talk", "en")
    assert [(s.number, s.title, s.notes) for s in text.slides] == [
        (1, "One", ""),
        (2, "Two", "notes for two"),
    ]


def test_a_deck_without_a_manifest_is_titled_by_its_first_slide(
    tmp_path: Path,
) -> None:
    lay_down(tmp_path, {"slides.md": _SLIDES})

    text = read_deck(tmp_path, _TALK)

    assert text is not None
    assert (text.title, text.language) == ("One", None)


def test_a_deck_without_a_build_has_no_words(tmp_path: Path) -> None:
    lay_down(tmp_path, {"slides.md": _SLIDES})

    text = texts_under(tmp_path).text_of(a_deck(_TALK), a_source("git@h:d.git"))

    assert text is None


def test_a_built_commit_without_slides_has_no_words(
    tmp_path: Path,
) -> None:
    lay_down(tmp_path, {"notes.txt": "nothing\n"})

    assert read_deck(tmp_path, _TALK) is None


@pytest.mark.parametrize("answering", [True, False], ids=["absent", "not-answering"])
def test_a_run_neither_here_nor_in_the_store_is_unavailable(
    tmp_path: Path, *, answering: bool
) -> None:
    deck = built(a_deck(_TALK), a_build(tmp_path))
    texts = texts_under(tmp_path, answering=answering)

    with pytest.raises(CoPresenterUnavailableError):
        texts.text_of(deck, a_source("git@heimserver:decks.git"))


def test_a_slides_file_that_is_a_link_gives_no_words(tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text(_SLIDES, encoding="utf-8")
    lay_down(tmp_path, {})
    run = tmp_path / "builds" / a_build(tmp_path).run.name
    (run / "deck" / "slides.md").symlink_to(outside)

    assert read_deck(tmp_path, _TALK) is None


def test_a_deck_whose_manifest_is_unreadable_is_still_read_by_its_slides(
    tmp_path: Path,
) -> None:
    lay_down(tmp_path, {"slides.md": _SLIDES, "deck.toml": "not a manifest\n"})

    text = read_deck(tmp_path, _TALK)

    assert text is not None
    assert (text.title, text.language) == ("One", None)


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        pytest.param(
            "---\ntheme: dark\n---\n# One\nbody\n\n---\n\n# Two\n",
            [(1, "One"), (2, "Two")],
            id="per-slide frontmatter is not a slide",
        ),
        pytest.param(
            "---\ntheme: dark\n# One\n",
            [(1, "One")],
            id="unclosed frontmatter stays in its slide",
        ),
        pytest.param(
            "# One\n```md\n---\n# not a slide\n```\n---\n\n# Two\n",
            [(1, "One"), (2, "Two")],
            id="a fence inside code is not a separator",
        ),
        pytest.param(
            "# One\n<!--\n---\nnotes\n-->\n\n---\n\n# Two\n",
            [(1, "One"), (2, "Two")],
            id="a fence inside a multiline comment is not a separator",
        ),
        pytest.param(
            "# One\n```\nnever closed\n---\n",
            [(1, "One")],
            id="an unclosed code fence runs to the end",
        ),
        pytest.param(
            "# One\n\n---\n\nplain words\n",
            [(1, "One"), (2, "plain words")],
            id="leading blank lines do not title a slide",
        ),
        pytest.param(
            "# One\n\n---\n\n---\n\n# Three\n",
            [(1, "One"), (2, "Three")],
            id="an empty slide is skipped",
        ),
        pytest.param(
            "# One\n\n---\n",
            [(1, "One")],
            id="a trailing separator opens no slide",
        ),
        pytest.param(
            "just words\n",
            [(1, "just words")],
            id="a slide without a heading is titled by its first line",
        ),
        pytest.param(
            "<!-- only notes -->\n",
            [(1, "Untitled")],
            id="a slide of notes alone is untitled",
        ),
        pytest.param(
            "#  \n",
            [(1, "#")],
            id="a heading line without words falls back to the first line",
        ),
    ],
)
def test_slides_follow_slidev_separators(
    tmp_path: Path,
    markdown: str,
    expected: list[tuple[int, str]],
) -> None:
    lay_down(tmp_path, {"slides.md": markdown})

    text = read_deck(tmp_path, _TALK)

    assert text is not None
    assert [(s.number, s.title) for s in text.slides] == expected


@pytest.mark.parametrize(
    ("markdown", "body", "notes"),
    [
        pytest.param(
            "# T\n<!-- first -->\n<!-- second -->\n",
            "# T\n<!-- first -->",
            "second",
            id="the last of two closed comments is the notes",
        ),
        pytest.param(
            "# T\n<!-- a <!-- b -->\n",
            "# T",
            "a <!-- b",
            id="a comment opened inside a comment ends at the first close",
        ),
        pytest.param(
            "# T\n<!-- n -->\ntext <!-- open\n",
            "# T\n\ntext <!-- open",
            "n",
            id="an unclosed opener after a closed comment stays in the body",
        ),
        pytest.param(
            "# T\n<!---->\nbody\n",
            "# T\n\nbody",
            "",
            id="an empty comment is empty notes and leaves the body",
        ),
        pytest.param(
            "# T\nbody\n",
            "# T\nbody",
            "",
            id="a slide without a comment has no notes",
        ),
    ],
)
def test_the_last_closed_comment_is_the_notes(
    tmp_path: Path,
    markdown: str,
    body: str,
    notes: str,
) -> None:
    """#476 cells 2-6: the last closed comment is the notes, as the regex read it."""
    lay_down(tmp_path, {"slides.md": markdown})

    text = read_deck(tmp_path, _TALK)

    assert text is not None
    assert (text.slides[0].body, text.slides[0].notes) == (body, notes)


def test_unclosed_comment_openers_are_read_in_linear_time(
    tmp_path: Path,
) -> None:
    """#476 cell 1: 160 KB of unclosed openers read within the bound."""
    lay_down(tmp_path, {"slides.md": "# T\n" + "<!--" * 40_000})

    started = time.monotonic()
    text = read_deck(tmp_path, _TALK)
    elapsed = time.monotonic() - started

    assert text is not None
    assert text.slides[0].notes == ""
    assert elapsed < _QUICK_SECONDS


def test_a_lone_hash_does_not_borrow_a_heading_from_a_later_line(
    tmp_path: Path,
) -> None:
    """#476 cell 8 (Decision 3): a heading is one line; a lone # takes no later one."""
    padding = " \n" * 50_000
    lay_down(tmp_path, {"slides.md": f"#{padding}# Found\n"})

    started = time.monotonic()
    text = read_deck(tmp_path, _TALK)
    elapsed = time.monotonic() - started

    assert text is not None
    assert text.slides[0].title == "Found"
    assert elapsed < _QUICK_SECONDS
