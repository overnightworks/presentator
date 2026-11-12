"""Judge the render probe's built talk and its exported PDF.

Usage: `uv run --locked python scripts/render_probe/judge.py TALK PDF`.

The talk is the built directory the probe's client copied out of the image; the
PDF is the export. It passes only when one file of the talk holds KaTeX's markup
class together with the formula's token (KaTeX's own bundle holds the class with
no formula, so the class alone does not pass), and when the PDF's text holds the
diagram's label, whitespace-normalised because the text layer wraps. A PDF that
cannot be read refuses. Its one output is the findings, one line each, and its
exit status is 0 when both hold and 1 when either does not.
"""

import argparse
import io
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

# Both values are written into the fixture deck in this directory's `deck/`.
FORMULA_LABEL = "probeNumerator"
DIAGRAM_LABEL = "Probe node alpha"
KATEX_CLASS = re.compile(rb"\bkatex\b")
PDF_MAGIC = b"%PDF-"
PASSED = 0
REFUSED = 1


class UnreadablePdfError(Exception):
    """The file is not a PDF this judge can read the text of."""


def find_formula(talk: Path) -> Path | None:
    """The first file of the talk that holds KaTeX's class and the formula's token."""
    for candidate in sorted(talk.rglob("*")):
        if not candidate.is_file():
            continue
        content = candidate.read_bytes()
        if KATEX_CLASS.search(content) and FORMULA_LABEL.encode() in content:
            return candidate
    return None


def pdf_text(document: Path) -> str:
    """The text every page of the PDF shows, or the refusal that it cannot be read."""
    try:
        content = document.read_bytes()
    except OSError as unreadable:
        message = f"the PDF cannot be read: {unreadable}"
        raise UnreadablePdfError(message) from unreadable
    if not content.startswith(PDF_MAGIC):
        message = "the file is not a PDF"
        raise UnreadablePdfError(message)
    try:
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() for page in reader.pages)
    except PyPdfError as unparsable:
        message = f"the PDF cannot be parsed: {unparsable}"
        raise UnreadablePdfError(message) from unparsable


def label_found(text: str, label: str) -> bool:
    """Whether the label reads in the text, wherever the text layer wrapped it."""
    return " ".join(label.split()) in " ".join(text.split())


def main(arguments: Sequence[str]) -> int:
    """Print what the judge found in the talk and the PDF; refuse what it did not."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("talk", type=Path)
    parser.add_argument("pdf", type=Path)
    parsed = parser.parse_args(arguments)
    refusals: list[str] = []
    formula = find_formula(parsed.talk)
    if formula is None:
        refusals.append(
            "refused: no file of the talk holds KaTeX's markup class with "
            f"{FORMULA_LABEL}"
        )
    else:
        sys.stdout.write(f"formula rendered in {formula.relative_to(parsed.talk)}\n")
    try:
        text = pdf_text(parsed.pdf)
    except UnreadablePdfError as unreadable:
        refusals.append(f"refused: {unreadable}")
    else:
        if label_found(text, DIAGRAM_LABEL):
            sys.stdout.write(f"diagram label found in the PDF: {DIAGRAM_LABEL}\n")
        else:
            refusals.append(f"refused: the PDF does not show the label {DIAGRAM_LABEL}")
    for refusal in refusals:
        sys.stdout.write(f"{refusal}\n")
    return REFUSED if refusals else PASSED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
