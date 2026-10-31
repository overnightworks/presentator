"""Catalog purity: no template or Svelte page carries a literal, user-facing string.

Owner: [ADR 0012](../docs/decisions/0012-themes-and-language.md), "Language is
an adapter". Every user-facing word in a Jinja2 lobby template must come from
the message catalog; typing a sentence into a template is the retrofit ADR
0012 exists to avoid. This script is a CI gate, not a package layer.

The rule is narrow and mechanical: it flags a literal (non-catalog) piece of
template text only where a person reads it — HTML text-node content, or one
of the human-readable attributes `title`, `placeholder`, `aria-label`, `alt`,
`label`, `aria-description`, `aria-roledescription`, `aria-valuetext`, a
`value` on a submit, button, or reset control (an `<input>` of that type, or a
`<button>` whose type is `submit` or omitted — the HTML default), and a
`content` on `<meta name="description">`. A plain `<input>`'s `value` is data
a person typed, not a label, and is left alone. Markup (tag names, other
attributes such as `class`, `href`, `id`, and a plain `<input>`'s `value`),
and content made only of digits or whitespace, are not text under this rule.

Jinja2's own parser draws that line for us. `jinja2.Environment.parse` returns
an AST in which every literal template character lives in a `TemplateData`
node and every substituted value is an expression node (`Name`, `Getattr`,
`Const`, ...); a catalog lookup is always a `Name`/`Getattr`, never a literal.
A hard-coded `{{ "Sign in" }}` bypasses the catalog exactly as much as the
same words typed straight into the template, so a `Const` string is treated
as literal text too, but only where it can be the expression's own runtime
value: the expression itself, a conditional expression's branch (`{{ "Yes" if
flag else "No" }}`), an `or`-chain's side (`{{ x or "Fallback" }}`), a `~`
concatenation's operand (`{{ "Welcome " ~ name }}`), or `default`'s first
positional argument (`{{ value | default("Sign in") }}`) — the one filter
whose argument becomes its own output when the piped value is empty. A
constant used as a subscript key (`{{ labels["home"] }}`) or as any other
call or filter argument never reaches the page as that expression's value, so
it is not followed. Walking that AST, rather than a regular expression over
the raw source, cannot be fooled by markup that looks like a sentence or by a
`{{ }}` that happens to look like plain text.

The AST's `Output` nodes are visited in document order (`find_all` walks
`If`/`For`/`Block` bodies in the order they are written) and fed, one after
another, into a single `html.parser.HTMLParser` for the whole template —
never reset between `Output` nodes. That single-scanner design is what lets a
`{% if %}` sitting inside a tag or an attribute value resolve correctly:
`HTMLParser.feed()` is built to buffer an incomplete tag across any number of
calls, so `<input{% if required %} required{% endif %}>` and `title="Name{%
if suffix %} ({{ suffix }}){% endif %}"` are seen as the one tag or attribute
they render as. Mutually exclusive branches (`if`/`else`, loop `else`) are fed
one after another as if both ran; that is a deliberate simplification, safe
for this check because it only ever asks "does literal text sit at a text or
flagged-attribute position", never "is the markup correctly nested".

Script and style bodies are excluded from the text-node scan: a `<script>`
or `<style>` element's content is code, not something a person reads as
prose, and flagging a JavaScript string literal here would be a false
positive this check has no business raising. The one persistent scanner pays
for that with a new failure mode: a `<script>` or `<style>` opened on one
branch of a template and never closed leaves the scanner inside it for the
rest of the file, so every literal after it would otherwise go unseen. That
is reported as its own finding — an open tag caught at end of file — rather
than a silent pass, because a scan that can be silently blinded is worse than
one that visibly refuses to trust itself.

Named gaps, not fixed here: a word that reaches the rendered page from Python
— a value computed or interpolated by a route rather than looked up in the
catalog — is invisible to this check, because it never appears as
`TemplateData` or a `Const` in any template's AST. So is a literal bound with
`{% set heading = "Sign in" %}` and rendered later through the bound name:
this check only ever looks at what an `Output` node renders, never at what a
`set` assigns, because following an assignment to every place its name is
later used is a dataflow analysis, not the narrow "does literal text sit at a
text or attribute position" this rule stays mechanical by asking. A template
scan can prove a template's own markup and expressions stay literal-free; it
cannot prove the whole rendered page does.

The lobby's Svelte pages under `frontend/src` are held to the same rule
(#185 I4). A `.svelte` file is markup with `{expression}` holes and a
`<script>` and `<style>` block. The blocks are code and are dropped; each hole
is folded the way a Jinja expression is: a hole that is only a string literal
(`{'Sign in'}`, or a template literal without a substitution) renders that
literal and counts as text, a logic block (`{#if}`, `{:else}`, `{/if}`,
`{@render}`) and every other expression render nothing the scan can see.
What is left goes through the same scanner, so the same text positions and
attributes are flagged, on an element and on a component alike. A kit
component's own words (appkit's `SignInPage`) are the kit's, not
presentator's (#185 I4): they ship inside the kit's package, which this scan
never reads, and what a page hands such a component is held like any other
markup. Named gap: a literal inside a larger expression (`{ok ? 'Saved' :
x}`) is not followed, because telling a shown string from a class name or a
key in JavaScript needs a JavaScript parser this check does not carry.

Rejected: a regular expression over the raw template source, which the task
that named this check already rejected — it cannot tell a `{{ catalog_value
}}` from a literal sentence, or a class name from a word a person reads. A
full HTML/DOM library was not warranted either: the stdlib `html.parser`
gives exactly the two callbacks (`handle_data`, `handle_starttag`) this
narrow rule needs, with no new dependency.
"""

from __future__ import annotations

import html
import re
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

import jinja2
from jinja2 import nodes

_TEMPLATES_DIRECTORY = Path("src") / "presentator" / "api" / "templates"
_TEMPLATE_GLOB = "*.html"
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
_DEFAULT_FILTER_NAME = "default"

_OFFENDER_MESSAGE = "{template}: {problem}"
_LITERAL_TEXT_PROBLEM = "literal text outside the catalog: {text!r}"
_PARSE_ERROR_PROBLEM = "cannot parse: {error}"
_UNCLOSED_RAW_TEXT_PROBLEM = (
    "<{tag}> is opened but never closed; everything after it is invisible to this check"
)
_NO_TEMPLATES_DIRECTORY_PROBLEM = "no templates directory"
_NO_TEMPLATE_FOUND_PROBLEM = "no template found"
_NO_SVELTE_DIRECTORY_PROBLEM = "no Svelte source directory"
_NO_SVELTE_PAGE_FOUND_PROBLEM = "no Svelte page found"
_UNCLOSED_SVELTE_EXPRESSION_PROBLEM = "an expression is opened but never closed"


@dataclass(frozen=True)
class CatalogPurityFinding:
    """One problem found under the templates or the Svelte pages.

    A literal string, a syntax error, an unclosed script/style tag or Svelte
    expression, or a missing or empty directory.
    """

    template_name: str
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


def _string_literal_values(expression: nodes.Node) -> tuple[str, ...]:
    """Return the string literals that can be this expression's own runtime value.

    Only descends into node types whose evaluated result is exactly one of
    their operands: a bare constant, a conditional expression's taken
    branch, an `or`-chain's left or right side, a `~` concatenation's
    operand, or the `default` filter's first positional argument (the one
    filter whose argument becomes its own output when the piped value is
    empty). A constant used as a subscript key (`labels["home"]`) or as any
    other call/filter argument never reaches the page as this expression's
    value, so those are not followed.
    """
    if isinstance(expression, nodes.Const):
        return (expression.value,) if isinstance(expression.value, str) else ()
    if isinstance(expression, nodes.CondExpr):
        branches = [expression.expr1]
        if expression.expr2 is not None:
            branches.append(expression.expr2)
        return tuple(
            literal for branch in branches for literal in _string_literal_values(branch)
        )
    if isinstance(expression, nodes.Or):
        return (
            *_string_literal_values(expression.left),
            *_string_literal_values(expression.right),
        )
    if isinstance(expression, nodes.Concat):
        return tuple(
            literal
            for operand in expression.nodes
            for literal in _string_literal_values(operand)
        )
    if isinstance(expression, nodes.Filter) and expression.name == _DEFAULT_FILTER_NAME:
        first_positional_argument = expression.args[0] if expression.args else None
        if first_positional_argument is not None:
            return _string_literal_values(first_positional_argument)
    return ()


def _reconstruct_fragment(children: tuple[nodes.Node, ...]) -> str:
    """Rebuild one `Output` run as HTML: literal chunks verbatim, expressions folded."""
    parts = [
        child.data
        if isinstance(child, nodes.TemplateData)
        else " ".join(_string_literal_values(child))
        for child in children
    ]
    return "".join(parts)


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


def _scan_template(template_ast: nodes.Template) -> _CatalogPurityScanner:
    scanner = _CatalogPurityScanner()
    for output in template_ast.find_all(nodes.Output):
        scanner.feed(_reconstruct_fragment(tuple(output.nodes)))
    scanner.close()
    return scanner


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
            template_name=name,
            problem=_LITERAL_TEXT_PROBLEM.format(text=text),
        )
        for text in scanner.literal_texts
    ]
    if scanner.open_raw_text_tag is not None:
        findings.append(
            CatalogPurityFinding(
                template_name=name,
                problem=_UNCLOSED_RAW_TEXT_PROBLEM.format(
                    tag=scanner.open_raw_text_tag
                ),
            )
        )
    return findings


def _template_findings(
    template_path: Path, relative_name: str, environment: jinja2.Environment
) -> list[CatalogPurityFinding]:
    source = template_path.read_text(encoding="utf-8")
    try:
        template_ast = environment.parse(source, filename=relative_name)
    except jinja2.TemplateSyntaxError as error:
        problem = _PARSE_ERROR_PROBLEM.format(error=error.message)
        return [CatalogPurityFinding(template_name=relative_name, problem=problem)]
    return _scanner_findings(_scan_template(template_ast), relative_name)


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
        return [CatalogPurityFinding(template_name=relative_name, problem=str(error))]
    scanner = _CatalogPurityScanner()
    scanner.feed(markup)
    scanner.close()
    return _scanner_findings(scanner, relative_name)


def _jinja_findings(repository: Path) -> list[CatalogPurityFinding]:
    environment = jinja2.Environment(autoescape=True)
    templates_directory = repository / _TEMPLATES_DIRECTORY
    try:
        template_paths = _require_paths(
            templates_directory,
            _TEMPLATE_GLOB,
            missing=_NO_TEMPLATES_DIRECTORY_PROBLEM,
            empty=_NO_TEMPLATE_FOUND_PROBLEM,
        )
    except FileNotFoundError as error:
        return [
            CatalogPurityFinding(
                template_name=_TEMPLATES_DIRECTORY.as_posix(), problem=str(error)
            )
        ]
    findings: list[CatalogPurityFinding] = []
    for template_path in template_paths:
        relative_name = template_path.relative_to(templates_directory).as_posix()
        findings.extend(_template_findings(template_path, relative_name, environment))
    return findings


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
                template_name=_SVELTE_DIRECTORY.as_posix(), problem=str(error)
            )
        ]
    findings: list[CatalogPurityFinding] = []
    for page_path in page_paths:
        relative_name = page_path.relative_to(repository).as_posix()
        findings.extend(_svelte_page_findings(page_path, relative_name))
    return findings


def find_catalog_purity_findings(repository: Path) -> tuple[CatalogPurityFinding, ...]:
    """Return every problem found under the templates and the Svelte pages.

    Names a directory itself when there is nothing to scan in it.
    """
    return (*_jinja_findings(repository), *_svelte_findings(repository))


def main() -> int:
    """Print each catalog purity finding and return 1 when any exist."""
    findings = find_catalog_purity_findings(Path.cwd())
    if not findings:
        return 0
    for finding in findings:
        sys.stdout.write(
            _OFFENDER_MESSAGE.format(
                template=finding.template_name, problem=finding.problem
            )
            + "\n"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
