"""The render probe's judge: a rendered formula in the talk, the label in the PDF."""

from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest

FORMULA_LABEL = "probeNumerator"
MARKUP = '<span class="katex"><span class="mord mathnormal">x</span></span>'
TALK_FILE = "assets/index-probe.js"
LABEL = "Probe node alpha"


def pdf_showing(lines: Sequence[str]) -> bytes:
    """A one-page PDF whose text is each of the lines, one under the other."""
    shown = " ".join(f"({line}) Tj 0 -24 Td" for line in lines)
    stream = f"BT /F1 18 Tf 72 720 Td {shown} ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n%b\nendstream" % (len(stream), stream),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document += b"%d 0 obj\n%b\nendobj\n" % (number, body)
    table_at = len(document)
    document += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        document += b"%010d 00000 n \n" % offset
    document += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        table_at,
    )
    return bytes(document)


def write_talk(directory: Path, *contents: str) -> Path:
    """A built talk whose one asset file holds each content."""
    asset = directory / TALK_FILE
    asset.parent.mkdir(parents=True)
    asset.write_text("".join(contents))
    return directory


@pytest.mark.parametrize(
    ("talk_contents", "pdf", "rendered"),
    [
        pytest.param(
            (MARKUP, FORMULA_LABEL),
            pdf_showing([LABEL]),
            True,
            id="formula_rendered_and_label_found_pass",
        ),
        pytest.param(
            (MARKUP,),
            pdf_showing([LABEL]),
            False,
            id="class_without_the_formula_token_refuses",
        ),
        pytest.param(
            (FORMULA_LABEL,),
            pdf_showing([LABEL]),
            False,
            id="token_without_the_class_refuses",
        ),
        pytest.param(
            (MARKUP, FORMULA_LABEL),
            pdf_showing(["Some other label"]),
            False,
            id="pdf_without_the_label_refuses",
        ),
        pytest.param(
            (MARKUP, FORMULA_LABEL),
            b"Probe node alpha, but no PDF",
            False,
            id="a_file_that_is_no_pdf_refuses",
        ),
        pytest.param(
            (MARKUP, FORMULA_LABEL),
            pdf_showing(["Probe node", "alpha"]),
            True,
            id="label_wrapped_across_lines_is_found",
        ),
    ],
)
def test_the_judge_passes_only_a_rendered_formula_and_a_found_label(
    tmp_path: Path,
    load_script: Callable[..., ModuleType],
    talk_contents: tuple[str, ...],
    pdf: bytes,
    rendered: bool,  # noqa: FBT001 - a parametrized row states its expectation
) -> None:
    judge = load_script("judge", "render_probe")
    talk = write_talk(tmp_path / "talk", *talk_contents)
    document = tmp_path / "deck.pdf"
    document.write_bytes(pdf)

    status = judge.main([str(talk), str(document)])

    assert (status == 0) is rendered


def test_an_unreadable_pdf_refuses_even_when_the_formula_rendered(
    tmp_path: Path,
    load_script: Callable[..., ModuleType],
) -> None:
    judge = load_script("judge", "render_probe")
    talk = write_talk(tmp_path / "talk", MARKUP, FORMULA_LABEL)
    document = tmp_path / "deck.pdf"
    document.write_bytes(b"%PDF-1.4\ntruncated, not a document")

    assert judge.main([str(talk), str(document)]) == 1
