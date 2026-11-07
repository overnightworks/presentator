"""Catalog purity: no Svelte page carries a literal, user-facing string.

Owner: the catalog rule in `docs/ARCHITECTURE.md` §5, "Words and look".
Every user-facing word on a lobby page must come from the message catalog;
typing a sentence into a page would make a second language a code change. This
script is a CI gate, not a package layer.

The rule is narrow and mechanical: it flags a literal (non-catalog) piece of
page text only where a person reads it — HTML text-node content, or one of the
human-readable attributes `title`, `placeholder`, `aria-label`, `alt`, `label`,
`aria-description`, `aria-roledescription`, `aria-valuetext`, a `value` on a
submit, button, or reset control (an `<input>` of that type, or a `<button>`
whose type is `submit` or omitted — the HTML default), and a `content` on
`<meta name="description">`. A plain `<input>`'s `value` is data a person
typed, not a label, and is left alone. Markup (tag names, other attributes
such as `class`, `href`, `id`, and a plain `<input>`'s `value`), and content
made only of digits or whitespace, are not text under this rule.

A `.svelte` file is markup with `{expression}` holes and a `<script>` and
`<style>` block. The blocks are code and are dropped; a hole that is only a
string literal (`{'Sign in'}`, or a template literal without a substitution)
renders that literal and counts as text, a logic block (`{#if}`, `{:else}`,
`{/if}`, `{@render}`) and every other expression render nothing the scan can
see. What is left goes through one `html.parser.HTMLParser`, so the same text
positions and attributes are flagged, on an element and on a component alike.
A kit component's built-in words (appkit's `SignInPage` without `words`) ship
inside the kit's package, which this scan never reads; presentator hands its
catalog's words to the kit, and what a page hands such a component is held like
any other markup. Named gap: a literal inside a larger expression
(`{ok ? 'Saved' : x}`) is not followed, because telling a shown string from a
class name or a key in JavaScript needs a JavaScript parser this check does
not carry.

Script and style bodies are excluded from the text-node scan: a `<script>` or
`<style>` element's content is code, not something a person reads as prose,
and flagging a JavaScript string literal here would be a false positive this
check has no business raising. The fold strips only closed blocks, so a
`<script>` or `<style>` opened and never closed leaves the scanner inside it
for the rest of the page and every literal after it would otherwise go unseen.
That is reported as its own finding — an open tag caught at end of file —
rather than a silent pass, because a scan that can be silently blinded is
worse than one that visibly refuses to trust itself.

Rejected: a regular expression over the raw page source, which cannot tell a
`{words.title}` from a literal sentence, or a class name from a word a person
reads. A full HTML/DOM library was not warranted either: the stdlib
`html.parser` gives exactly the two callbacks (`handle_data`,
`handle_starttag`) this narrow rule needs, with no new dependency.
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

_SVELTE_DIRECTORY = Path("frontend") / "src"
_SVELTE_GLOB = "*.svelte"
_SVELTE_CODE_BLOCK = re.compile(
    r"<(script|style)(?:\s[^>]*)?>.*?</\1\s*>", re.DOTALL | re.IGNORECASE
)
_SVELTE_LOGIC_MARKS = frozenset("#:/@")
_QUOTES = frozenset("'\"`")

_ALWAYS_FLAGGED_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "title",
        "placeholder",
        "aria-label",
        "alt",
        "label",
        "aria-description",
        "aria-roledescription",
        "aria-valuetext",
    }
)
_INPUT_TYPES_WITH_A_READ_LABEL_VALUE: frozenset[str] = frozenset(
    {"submit", "button", "reset"}
)
_RAW_TEXT_TAGS: frozenset[str] = frozenset({"script", "style"})

_OFFENDER_MESSAGE = "{page}: {problem}"
_LITERAL_TEXT_PROBLEM = "literal text outside the catalog: {text!r}"
_UNCLOSED_RAW_TEXT_PROBLEM = (
    "<{tag}> is opened but never closed; everything after it is invisible to this check"
)
_NO_SVELTE_DIRECTORY_PROBLEM = "no Svelte source directory"
_NO_SVELTE_PAGE_FOUND_PROBLEM = "no Svelte page found"
_UNCLOSED_SVELTE_EXPRESSION_PROBLEM = "an expression is opened but never closed"


@dataclass(frozen=True)
class CatalogPurityFinding:
    """One problem found under the Svelte pages.

    A literal string, an unclosed script/style tag or Svelte expression, or a
    missing or empty directory.
    """

    page_name: str
    problem: str


def _is_user_facing_text(candidate: str) -> bool:
    stripped = candidate.strip()
    return bool(stripped) and any(character.isalpha() for character in stripped)


def _value_attribute_is_flagged(tag: str, type_value: str | None) -> bool:
    """A submit/button/reset value is a read label; a plain input's value is data."""
    if tag == "input":
        return type_value in _INPUT_TYPES_WITH_A_READ_LABEL_VALUE
    if tag == "button":
        return type_value is None or type_value == "submit"
    return False


def _flagged_attribute_names(
    tag: str, values_by_name: dict[str, str | None]
) -> frozenset[str]:
    names = set(_ALWAYS_FLAGGED_ATTRIBUTES)
    if _value_attribute_is_flagged(tag, values_by_name.get("type")):
        names.add("value")
    if tag == "meta" and values_by_name.get("name") == "description":
        names.add("content")
    return frozenset(names)


class _CatalogPurityScanner(HTMLParser):
    """Collects literal text at text-node and flagged-attribute positions."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.literal_texts: list[str] = []
        self.open_raw_text_tag: str | None = None

    def handle_data(self, data: str) -> None:
        """Record text-node content, ignoring script/style bodies."""
        if self.open_raw_text_tag is not None:
            return
        if _is_user_facing_text(data):
            self.literal_texts.append(data.strip())

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record flagged attribute values and enter a script/style body."""
        values_by_name = dict(attrs)
        for name in _flagged_attribute_names(tag, values_by_name):
            value = values_by_name.get(name)
            if value is not None and _is_user_facing_text(value):
                self.literal_texts.append(value.strip())
        if tag in _RAW_TEXT_TAGS:
            self.open_raw_text_tag = tag

    def handle_endtag(self, tag: str) -> None:
        """Leave a script/style body once it closes."""
        if tag == self.open_raw_text_tag:
            self.open_raw_text_tag = None


def _require_paths(
    directory: Path, glob: str, *, missing: str, empty: str
) -> tuple[Path, ...]:
    """Return every path under the directory that matches, sorted; refuse silence."""
    if not directory.is_dir():
        raise FileNotFoundError(missing)
    paths = tuple(sorted(directory.rglob(glob)))
    if not paths:
        raise FileNotFoundError(empty)
    return paths


def _scanner_findings(
    scanner: _CatalogPurityScanner, name: str
) -> list[CatalogPurityFinding]:
    findings = [
        CatalogPurityFinding(
            page_name=name,
            problem=_LITERAL_TEXT_PROBLEM.format(text=text),
        )
        for text in scanner.literal_texts
    ]
    if scanner.open_raw_text_tag is not None:
        findings.append(
            CatalogPurityFinding(
                page_name=name,
                problem=_UNCLOSED_RAW_TEXT_PROBLEM.format(
                    tag=scanner.open_raw_text_tag
                ),
            )
        )
    return findings


def _end_of_quoted(source: str, opening: int) -> int:
    """The index of the quote that closes the string opened at `opening`, or -1."""
    quote = source[opening]
    index = opening + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == quote:
            return index
        index += 1
    return -1


def _closing_brace(source: str, opening: int) -> int:
    """The index of the `}` that closes the hole opened at `opening`, or -1."""
    depth = 0
    index = opening
    while index < len(source):
        character = source[index]
        if character in _QUOTES:
            index = _end_of_quoted(source, index)
            if index == -1:
                return -1
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _only_a_string_literal(expression: str) -> str | None:
    """The literal's text when the expression is one string literal, else None."""
    stripped = expression.strip()
    if not stripped or stripped[0] not in _QUOTES:
        return None
    if _end_of_quoted(stripped, 0) != len(stripped) - 1:
        return None
    literal = stripped[1:-1]
    return None if stripped[0] == "`" and "${" in literal else literal


def _folded_hole(expression: str) -> str:
    if expression[:1] in _SVELTE_LOGIC_MARKS:
        return ""
    return _only_a_string_literal(expression) or ""


def _fold_svelte_markup(source: str) -> str:
    """The page's markup as HTML, each hole folded to what the scan may see.

    A hole right after `=` is an unquoted attribute value, so its text is
    quoted to stay one value. A hole that never closes is a ValueError.
    """
    markup = _SVELTE_CODE_BLOCK.sub("", source)
    parts: list[str] = []
    position = 0
    while (opening := markup.find("{", position)) != -1:
        closing = _closing_brace(markup, opening)
        if closing == -1:
            raise ValueError(_UNCLOSED_SVELTE_EXPRESSION_PROBLEM)
        parts.append(markup[position:opening])
        shown = html.escape(_folded_hole(markup[opening + 1 : closing]))
        is_attribute_value = markup[:opening].rstrip().endswith("=")
        parts.append(f'"{shown}"' if is_attribute_value else shown)
        position = closing + 1
    parts.append(markup[position:])
    return "".join(parts)


def _svelte_page_findings(
    page_path: Path, relative_name: str
) -> list[CatalogPurityFinding]:
    try:
        markup = _fold_svelte_markup(page_path.read_text(encoding="utf-8"))
    except ValueError as error:
        return [CatalogPurityFinding(page_name=relative_name, problem=str(error))]
    scanner = _CatalogPurityScanner()
    scanner.feed(markup)
    scanner.close()
    return _scanner_findings(scanner, relative_name)


def _svelte_findings(repository: Path) -> list[CatalogPurityFinding]:
    try:
        page_paths = _require_paths(
            repository / _SVELTE_DIRECTORY,
            _SVELTE_GLOB,
            missing=_NO_SVELTE_DIRECTORY_PROBLEM,
            empty=_NO_SVELTE_PAGE_FOUND_PROBLEM,
        )
    except FileNotFoundError as error:
        return [
            CatalogPurityFinding(
                page_name=_SVELTE_DIRECTORY.as_posix(), problem=str(error)
            )
        ]
    findings: list[CatalogPurityFinding] = []
    for page_path in page_paths:
        relative_name = page_path.relative_to(repository).as_posix()
        findings.extend(_svelte_page_findings(page_path, relative_name))
    return findings


def find_catalog_purity_findings(repository: Path) -> tuple[CatalogPurityFinding, ...]:
    """Return every problem found under the Svelte pages.

    Names the directory itself when there is nothing to scan in it.
    """
    return tuple(_svelte_findings(repository))


def main() -> int:
    """Print each catalog purity finding and return 1 when any exist."""
    findings = find_catalog_purity_findings(Path.cwd())
    if not findings:
        return 0
    for finding in findings:
        sys.stdout.write(
            _OFFENDER_MESSAGE.format(page=finding.page_name, problem=finding.problem)
            + "\n"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
