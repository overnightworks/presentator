# ruff: noqa: T201 - the probe's one output is the line it prints for its test
"""Run the co-presenter's language owners over a words module of other catalogs.

`python -m tests.presentator.host.support.further_language <words.py> <tag>`
prints, as one JSON object, what the owners decide for `tag`: the offered
language, the prompt for a slide outside the deck, and the start's refusal.
"""

import importlib.util
import json
import sys
from pathlib import Path

_WORDS_MODULE = "presentator.contracts.words"


def _install_words(path: Path) -> None:
    spec = importlib.util.spec_from_file_location(_WORDS_MODULE, path)
    if spec is None or spec.loader is None:
        message = f"{path} is no Python module"
        raise ImportError(message)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_WORDS_MODULE] = module
    spec.loader.exec_module(module)


def main() -> None:
    """Install the generated words, then ask each owner about the tag."""
    words, tag = Path(sys.argv[1]), sys.argv[2]
    _install_words(words)
    # Each owner is imported only now, so it reads the installed words module.
    from presentator.application.copresenter_prompt import user_message  # noqa: PLC0415
    from presentator.application.preferences import offered_language  # noqa: PLC0415
    from presentator.contracts.copresenter import DeckText, Question  # noqa: PLC0415
    from presentator.host.config import load_settings  # noqa: PLC0415
    from presentator.host.environment import ConfigurationError  # noqa: PLC0415

    offered = offered_language(tag)
    deck = DeckText(title="Deck", language=tag, slides=())
    prompt = user_message(Question(said="?", slide=2, language=offered), deck)
    try:
        load_settings()
    except ConfigurationError as refused:
        reason: str | None = str(refused)
        variables = list(refused.variables)
    else:
        reason, variables = None, []
    print(
        json.dumps(
            {
                "offered": offered,
                "prompt": prompt,
                "refused_reason": reason,
                "refused_variables": variables,
            }
        )
    )


if __name__ == "__main__":
    main()
