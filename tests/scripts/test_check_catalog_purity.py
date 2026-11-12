"""Catalog purity check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_CHECK_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_catalog_purity.py"
)
_SVELTE_DIRECTORY = Path("frontend") / "src"
_SVELTE_PAGE = _SVELTE_DIRECTORY / "routes" / "+page.svelte"


def _write(repository: Path, relative: Path, body: str) -> None:
    path = repository / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _run_check(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT)],
        check=False,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("page_body", "expected_stdout"),
    [
        pytest.param(
            '<script lang="ts">\n'
            "  let { data } = $props();\n"
            "  const label = 'Not shown';\n"
            "</script>\n"
            "<svelte:head><title>{data.words.title}</title></svelte:head>\n"
            "<SignInPage appName={data.words.wordmark}"
            " state={{ page: 'signed-out', signInHref: data.start }}"
            " words={data.kitWords} />\n"
            "<button aria-label={data.words.close}>{data.words.close}</button>\n"
            "{#if data.open}<p>{data.words.open}</p>"
            "{:else}<p>{data.words.shut}</p>{/if}\n"
            "<p>{`${data.words.greeting}, ${data.name}`}</p>\n"
            "<style>.hero { color: red; }</style>\n",
            "",
            id="expressions-blocks-script-and-style-pass",
        ),
        pytest.param(
            "<h1>Log in</h1>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Log in'\n",
            id="literal-text-node-is-flagged",
        ),
        pytest.param(
            "<p>{'Saved'}</p>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Saved'\n",
            id="a-hole-holding-only-a-string-literal-is-flagged",
        ),
        pytest.param(
            "<p>{`Saved`}</p>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Saved'\n",
            id="a-template-literal-without-substitution-is-flagged",
        ),
        pytest.param(
            "<button aria-label={'Close the menu'}>{words.close}</button>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog:"
            " 'Close the menu'\n",
            id="unquoted-literal-attribute-value-is-flagged-whole",
        ),
        pytest.param(
            '<Dialog title="Remove source" />\n',
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog:"
            " 'Remove source'\n",
            id="literal-flagged-attribute-on-a-component-is-flagged",
        ),
        pytest.param(
            "<svelte:head><title>Decks</title></svelte:head>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Decks'\n",
            id="literal-page-title-is-flagged",
        ),
        pytest.param(
            '<input type="submit" value="Send" />\n',
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Send'\n",
            id="a-submit-input-value-is-flagged",
        ),
        pytest.param(
            '<input type="button" value="Open" />\n',
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Open'\n",
            id="a-button-input-value-is-flagged",
        ),
        pytest.param(
            '<input type="reset" value="Clear" />\n',
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Clear'\n",
            id="a-reset-input-value-is-flagged",
        ),
        pytest.param(
            '<button value="Save">{words.save}</button>\n',
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog: 'Save'\n",
            id="a-button-without-a-type-is-a-submit-and-its-value-is-flagged",
        ),
        pytest.param(
            '<input type="text" value="draft" />\n',
            "",
            id="a-plain-input-value-is-data-and-passes",
        ),
        pytest.param(
            '<button type="button" value="draft">{words.save}</button>\n',
            "",
            id="a-typed-button-value-passes",
        ),
        *[
            pytest.param(
                f'<div {attribute}="Some words"></div>\n',
                f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog:"
                " 'Some words'\n",
                id=f"{attribute}-is-flagged",
            )
            for attribute in (
                "label",
                "aria-description",
                "aria-roledescription",
                "aria-valuetext",
            )
        ],
        pytest.param(
            '<svelte:head><meta name="description" content="About us" />'
            "</svelte:head>\n",
            f"{_SVELTE_PAGE.as_posix()}: literal text outside the catalog:"
            " 'About us'\n",
            id="a-meta-description-is-flagged",
        ),
        pytest.param(
            '<div class="hero" id="top" href="/x"><b>42</b> <i>  </i></div>\n',
            "",
            id="markup-digits-and-unflagged-attributes-pass",
        ),
        pytest.param(
            "<div><script>\n",
            f"{_SVELTE_PAGE.as_posix()}: <script> is opened but never closed;"
            " everything after it is invisible to this check\n",
            id="an-unclosed-script-fails-loud",
        ),
        pytest.param(
            "<p>{words.title</p>\n",
            f"{_SVELTE_PAGE.as_posix()}: an expression is opened but never closed\n",
            id="an-unclosed-expression-is-reported",
        ),
    ],
)
def test_catalog_purity_check_reports_whether_every_svelte_page_stays_literal_free(
    tmp_path: Path, page_body: str, expected_stdout: str
) -> None:
    _write(tmp_path, _SVELTE_PAGE, page_body)

    completed = _run_check(tmp_path)

    assert completed.returncode == (1 if expected_stdout else 0)
    assert completed.stdout == expected_stdout


def test_catalog_purity_check_reports_a_missing_directory_without_crashing(
    tmp_path: Path,
) -> None:
    completed = _run_check(tmp_path)

    assert completed.returncode == 1
    assert completed.stdout == "frontend/src: no Svelte source directory\n"
    assert completed.stderr == ""


def test_catalog_purity_check_reports_an_empty_directory_without_crashing(
    tmp_path: Path,
) -> None:
    (tmp_path / _SVELTE_DIRECTORY).mkdir(parents=True)

    completed = _run_check(tmp_path)

    assert completed.returncode == 1
    assert completed.stdout == "frontend/src: no Svelte page found\n"
    assert completed.stderr == ""


def test_catalog_purity_check_passes_on_the_real_pages() -> None:
    repository_root = Path(__file__).resolve().parents[2]

    completed = _run_check(repository_root)

    assert completed.returncode == 0
    assert completed.stdout == ""
