"""Production names that something other than a Python call site in `src` reaches.

vulture reads `src` and this file alone, so a test calling a name never keeps
it alive. A Svelte page is not Python, so a field only a page reads looks
unused; a library's or a standard-library base class's own dispatch is
invisible the same way. Every entry names the site that reaches it; an entry
without one is an excuse.
"""

from importlib import import_module

from presentator.adapters.postgres.accounts import PostgresSubjectUserStore
from presentator.adapters.postgres.sessions import PostgresSessionRecordStore
from presentator.agent.settings import AgentSettings
from presentator.api.copresenter import AskRequest
from presentator.api.decks import TalkFiles
from presentator.api.lobby_shell import _InlineScripts  # pyright: ignore[reportPrivateUsage] - HTMLParser.feed, not Python code, calls its hooks
from presentator.api.problems import ValidationFault
from presentator.api.session_api import SessionAddresses, SessionView
from presentator.contracts import words
from presentator.contracts.people import DeckExport
from presentator.contracts.preferences import ThemeChoice
from presentator.host import console
from presentator.host.config import Settings
from presentator.host.worker_settings import WorkerSettings

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

# Pydantic reads these fields when it writes a validation answer.
ValidationFault.loc
ValidationFault.msg

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

# webauth's sign-in and session dependencies call these store-port methods
# (`webauth.ports`) on the stores the composition root hands it.
PostgresSubjectUserStore.create_for_subject
PostgresSubjectUserStore.set_role
PostgresSubjectUserStore.set_kind
PostgresSessionRecordStore.load
PostgresSessionRecordStore.touch
PostgresSessionRecordStore.replace_refresh_token
PostgresSessionRecordStore.prune_overflow

# Pydantic writes these fields into `GET /api/session`; the lobby pages read them.
SessionAddresses.logout
SessionAddresses.talk_start
SessionView.sign_in

# `dataclasses.asdict` writes every field of a person's data export.
DeckExport.built_commit

# Pydantic and the preference store look a chosen theme up by its value.
ThemeChoice.LIGHT
ThemeChoice.DARK
