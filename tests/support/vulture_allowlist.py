"""Names that something other than a Python call site reaches.

A Svelte page is not Python, so a field only a page reads looks unused; a
standard-library base class's own dispatch is invisible the same
way. Every entry names the site that reaches it; an entry without one is an
excuse.
"""

from importlib import import_module

from presentator.agent.settings import AgentSettings
from presentator.api.copresenter import AskRequest
from presentator.api.decks import TalkFiles
from presentator.api.lobby_shell import _InlineScripts  # pyright: ignore[reportPrivateUsage] - HTMLParser.feed, not Python code, calls its hooks
from presentator.api.problems import ValidationFault
from presentator.contracts import words
from presentator.host import console
from presentator.host.config import Settings
from presentator.host.worker_settings import WorkerSettings
from tests.conftest import redis_environment, served_database
from tests.gitmirror.test_mirror import DumbHttpHandler, RespondingHandler
from tests.presentator.host.conftest import (
    clients_closed_with_the_test,
    logging_undone_after_main,
)
from tests.presentator.host.support.held_start import sitecustomize
from tests.presentator.host.test_main import every_tick_polls_every_source, pytestmark
from tests.presentator.host.test_worker_settings import no_worker_variables
from tests.support.lobby_drive import AdmitEveryRequest, DeckRecord

# `BaseHTTPRequestHandler`'s own request dispatch calls this by name; nothing
# in the test that stands the server up ever calls it itself.
DumbHttpHandler.log_message
RespondingHandler.log_message

# webauth's `IpRateLimitMiddleware` reaches the backend through `config.rate_limits`.
AdmitEveryRequest.is_allowed

# `HTMLParser.feed` dispatches to these by name; nothing calls them directly.
_InlineScripts.handle_starttag
_InlineScripts.handle_data
_InlineScripts.handle_endtag

# `StaticFiles.__call__` dispatches to this override for every talk request.
TalkFiles.get_response

# `StaticFiles.__call__` runs this no-op override instead of the base directory check.
TalkFiles.check_config

# Pydantic runs this `model_validator` before every `AgentSettings` validation.
AgentSettings.drop_the_retired_backend

# Pydantic runs this `field_validator` on every `AskRequest.language` value.
AskRequest.empty_language_is_no_language

# pydantic reads a model's `model_config` by name when the class is built.
WorkerSettings.model_config

# Pydantic runs this `model_validator` after every `Settings` validation.
Settings._two_hostnames  # noqa: SLF001 - pydantic, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]
Settings._worker_is_reached  # noqa: SLF001 - pydantic, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]
Settings._voices_speak_their_language  # noqa: SLF001 - pydantic, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]
Settings._statement_bound_fits_postgres  # noqa: SLF001 - pydantic, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]

# pytest applies this autouse fixture to every host test by itself.
logging_undone_after_main  # pyright: ignore[reportUnusedExpression]

# pytest applies this autouse fixture to every composition-root test by itself.
every_tick_polls_every_source  # pyright: ignore[reportUnusedExpression]

# pytest reads `pytestmark` of each module and finds the fixture it names by its string.
pytestmark  # pyright: ignore[reportUnusedExpression]
redis_environment  # pyright: ignore[reportUnusedExpression]
served_database  # pyright: ignore[reportUnusedExpression]

# pytest applies this autouse fixture to every worker-settings test by itself.
no_worker_variables  # pyright: ignore[reportUnusedExpression]

# pytest applies this autouse fixture to every serving-loop test by itself.
clients_closed_with_the_test  # pyright: ignore[reportUnusedExpression]

# Pydantic reads these fields when it writes a validation answer.
ValidationFault.loc
ValidationFault.msg

# Pydantic calls the drive's deck-record validator when a request is parsed.
DeckRecord.builds_fit_the_state

# The generated words module names the language its catalog is written in; the
# server reads only its offered languages and texts.
words.SOURCE_LANGUAGE

# Alembic reads `down_revision` off each revision module by name.
import_module(
    "presentator.adapters.postgres.migrations.versions.0001_baseline"
).down_revision

# pyproject.toml's `[project.scripts]` names these as the two console scripts.
console.presentator
console.presentator_worker

# The import system calls a finder on `sys.meta_path` by this name.
sitecustomize._HoldBeforeImport.find_spec  # noqa: SLF001 - the import system, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]
