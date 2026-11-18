"""The words of a deck, read from the run its talk was built in.

Slidev owns the real parser. This reader is the honest subset a voice needs:
Slidev's `---` separators and per-slide frontmatter, a heading as the title,
the last HTML comment as speaker notes. It is not a second slide map.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from presentator.adapters.build_cache import BuildCache
from presentator.adapters.build_tree import opened_without_a_link
from presentator.adapters.deck_manifest import (
    DeckManifest,
    MalformedManifestError,
    read_manifest,
)
from presentator.contracts.copresenter import (
    CoPresenterUnavailableError,
    DeckText,
    SlideText,
)
from presentator.contracts.decks import (
    DECK_DIRECTORY,
    MANIFEST_FILE,
    SLIDES_FILE,
    Deck,
    Source,
)

_SEPARATOR: Final = "---"
_FENCE_OPEN = re.compile(r"^\s*`+")
_NOTE_OPEN: Final = "<!--"
_NOTE_CLOSE: Final = "-->"
_TITLE_LINE = re.compile(r"^#[ \t]+(\S.*)$", re.MULTILINE)
_NO_MANIFEST = DeckManifest(title=None, language=None)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuiltDeckTexts:
    """Reads a deck's words out of the run its standing talk was built in."""

    cache: BuildCache

    def text_of(self, deck: Deck, source: Source) -> DeckText | None:
        """The deck's title, language and slides at its built commit.

        Nothing while the deck has no build or its commit carries no slides;
        a run the store cannot hand over is unavailable.
        """
        del source
        if deck.build is None:
            return None
        run = self.cache.local(deck.build.run.name)
        if run is None:
            raise CoPresenterUnavailableError
        under = self.cache.root.absolute()
        try:
            markdown = _read(run / DECK_DIRECTORY / SLIDES_FILE, under=under)
            if markdown is None:
                return None
            text = markdown.decode()
        except UnicodeDecodeError as unreadable:
            raise CoPresenterUnavailableError from unreadable
        slides = _slides(text)
        manifest = _manifest(
            _read(run / DECK_DIRECTORY / MANIFEST_FILE, under=under), folder=deck.slug
        )
        return DeckText(
            title=manifest.title or (slides[0].title if slides else deck.slug),
            language=manifest.language,
            slides=slides,
        )


def _read(written: Path, *, under: Path) -> bytes | None:
    """That file of the run, or nothing where it is none: the deck's author wrote it."""
    found = opened_without_a_link(written.absolute(), under=under)
    if found is None:
        return None
    with os.fdopen(found, "rb") as file:
        return file.read()


def _manifest(manifest: bytes | None, *, folder: str) -> DeckManifest:
    if manifest is None:
        return _NO_MANIFEST
    try:
        return read_manifest(manifest, folder=folder)
    except MalformedManifestError:
        return _NO_MANIFEST


def _slides(markdown: str) -> tuple[SlideText, ...]:
    slides: list[SlideText] = []
    number = 1
    for chunk in _slide_chunks(markdown):
        content, notes = _split_notes(_without_frontmatter(chunk))
        if not content.strip() and not notes:
            continue
        slides.append(
            SlideText(
                number=number,
                title=_title(content),
                body=content.strip(),
                notes=notes,
            )
        )
        number += 1
    return tuple(slides)


def _slide_chunks(markdown: str) -> list[str]:
    """Split on Slidev's slide separators, keeping per-slide frontmatter.

    A line of exactly `---` starts the next slide. When the following line is
    non-empty, that fence opens YAML frontmatter and the matching `---` closes
    it — it is not a slide of its own. Fences inside HTML comments or fenced
    code are not separators.
    """
    lines = markdown.splitlines()
    chunks: list[str] = []
    start = 0
    in_html_comment = False
    index = 0
    length = len(lines)
    while index < length:
        raw_line = lines[index]
        line = raw_line.rstrip()
        if in_html_comment:
            in_html_comment = _html_comment_continues(raw_line, inside=True)
            index += 1
            continue
        if line.startswith(_SEPARATOR):
            _collect_chunk(chunks, lines, start, index)
            next_line = lines[index + 1] if index + 1 < length else ""
            opens_frontmatter = _opens_frontmatter(line) and bool(next_line.strip())
            start = index if opens_frontmatter else index + 1
            index = _after_separator(lines, index, opens_frontmatter=opens_frontmatter)
            continue
        marker = _code_fence_marker(line)
        if marker is not None:
            index = _after_code_fence(lines, index + 1, marker)
            continue
        in_html_comment = _html_comment_continues(raw_line, inside=False)
        index += 1
    if start < length:
        _collect_chunk(chunks, lines, start, length)
    return chunks


def _after_separator(lines: list[str], index: int, *, opens_frontmatter: bool) -> int:
    if not opens_frontmatter:
        return index + 1
    index += 1
    while index < len(lines) and lines[index].rstrip() != _SEPARATOR:
        index += 1
    return index + 1


def _code_fence_marker(line: str) -> str | None:
    fence = _FENCE_OPEN.match(line)
    if fence is None or not line.lstrip().startswith("```"):
        return None
    return fence.group(0)


def _after_code_fence(lines: list[str], index: int, marker: str) -> int:
    while index < len(lines) and not lines[index].startswith(marker):
        index += 1
    return min(index + 1, len(lines))


def _opens_frontmatter(line: str) -> bool:
    return not line.startswith("----")


def _collect_chunk(chunks: list[str], lines: list[str], start: int, end: int) -> None:
    if start >= end:
        return
    chunks.append("\n".join(lines[start:end]))


def _without_frontmatter(chunk: str) -> str:
    lines = chunk.splitlines()
    if not lines or lines[0].strip() != _SEPARATOR:
        return chunk
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _SEPARATOR:
            return "\n".join(lines[index + 1 :])
    return chunk


def _html_comment_continues(line: str, *, inside: bool) -> bool:
    cursor = 0
    while cursor < len(line):
        if inside:
            end = line.find(_NOTE_CLOSE, cursor)
            if end < 0:
                return True
            inside = False
            cursor = end + len(_NOTE_CLOSE)
            continue
        start = line.find(_NOTE_OPEN, cursor)
        if start < 0:
            return False
        end = line.find(_NOTE_CLOSE, start + len(_NOTE_OPEN))
        if end < 0:
            return True
        cursor = end + len(_NOTE_CLOSE)
    return inside


def _split_notes(chunk: str) -> tuple[str, str]:
    last: tuple[int, int] | None = None
    cursor = 0
    while (start := chunk.find(_NOTE_OPEN, cursor)) != -1:
        end = chunk.find(_NOTE_CLOSE, start + len(_NOTE_OPEN))
        if end == -1:
            break
        last = (start, end)
        cursor = end + len(_NOTE_CLOSE)
    if last is None:
        return chunk, ""
    start, end = last
    notes = chunk[start + len(_NOTE_OPEN) : end].strip()
    return f"{chunk[:start]}{chunk[end + len(_NOTE_CLOSE) :]}", notes


def _title(body: str) -> str:
    match = _TITLE_LINE.search(body)
    if match:
        return match.group(1).strip()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:80]
    return "Untitled"
