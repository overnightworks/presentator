"""A further co-presenter language is its catalog and its voice entry, no code."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[3]
_CATALOGS = _REPOSITORY / "frontend" / "src" / "lib" / "catalogs"
_SUBPROCESS_SECONDS = 60
# Dutch shares English's plural categories, so the English texts translate it.
_FURTHER = "nl"
_FURTHER_VOICE_VARIABLE = f"PRESENTATOR_SPEECH_VOICES__{_FURTHER.upper()}"


def _words_with_a_further_language(project: Path) -> Path:
    """The words module the generator writes from the catalogs plus a Dutch one."""
    catalogs = project / "catalogs"
    shutil.copytree(_CATALOGS, catalogs)
    english = (catalogs / "en.po").read_text(encoding="utf-8")
    further = english.replace('"Language: en\\n"', f'"Language: {_FURTHER}\\n"')
    (catalogs / f"{_FURTHER}.po").write_text(further, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[tool.design-catalogs]\n"
        'catalogs = "catalogs"\ntypescript = "words"\npython = "words.py"\n',
        encoding="utf-8",
    )
    subprocess.run(  # noqa: S603 - the generator beside this interpreter
        [Path(sys.executable).with_name("design-catalogs")],
        cwd=project,
        capture_output=True,
        timeout=_SUBPROCESS_SECONDS,
        check=True,
    )
    return project / "words.py"


@dataclass(frozen=True, slots=True, kw_only=True)
class _Decided:
    """What the owners decide for the further language, as the probe printed it."""

    offered: str
    prompt: str
    refused_reason: str | None
    refused_variables: tuple[str, ...]


def _decided(words: Path) -> _Decided:
    probed = subprocess.run(  # noqa: S603 - the interpreter and this repository's probe
        [
            sys.executable,
            "-m",
            "tests.presentator.host.support.further_language",
            str(words),
            _FURTHER,
        ],
        env={**os.environ, "PYTHONPATH": str(_REPOSITORY)},
        capture_output=True,
        text=True,
        timeout=_SUBPROCESS_SECONDS,
        check=True,
    )
    printed = json.loads(probed.stdout)
    return _Decided(
        offered=printed["offered"],
        prompt=printed["prompt"],
        refused_reason=printed["refused_reason"],
        refused_variables=tuple(printed["refused_variables"]),
    )


@pytest.fixture
def further_words(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return _words_with_a_further_language(project)


@pytest.mark.usefixtures("environment")
def test_a_further_catalog_language_is_offered_and_named_in_the_prompt(
    further_words: Path,
) -> None:
    decided = _decided(further_words)

    assert decided.offered == _FURTHER
    assert "Slide 2 is not in this deck." in decided.prompt
    assert decided.prompt.endswith(
        f"Answer in the language whose BCP 47 tag is {_FURTHER}."
    )


@pytest.mark.usefixtures("environment")
def test_a_further_catalog_language_without_a_voice_entry_refuses_the_start(
    further_words: Path,
) -> None:
    decided = _decided(further_words)

    assert decided.refused_variables == (_FURTHER_VOICE_VARIABLE,)
    assert "no voice" in str(decided.refused_reason)


def test_the_voice_entry_of_a_further_language_is_judged_by_the_speech_catalogue(
    environment: pytest.MonkeyPatch, further_words: Path
) -> None:
    environment.setenv(_FURTHER_VOICE_VARIABLE, "ryan")

    decided = _decided(further_words)

    assert decided.refused_variables == (_FURTHER_VOICE_VARIABLE,)
    assert "does not speak" in str(decided.refused_reason)
