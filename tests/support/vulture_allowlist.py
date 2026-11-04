"""Names that something other than a Python call site reaches.

A Jinja template is not Python, so a field only a template renders looks
unused; a standard-library base class's own dispatch is invisible the same
way. Every entry names the site that reaches it; an entry without one is an
excuse.
"""

from presentator.api.copresenter import AskRequest
from presentator.api.decks import TalkFiles
from presentator.api.lobby_shell import _InlineScripts  # pyright: ignore[reportPrivateUsage] - HTMLParser.feed, not Python code, calls its hooks
from presentator.api.problems import ValidationFault
from presentator.api.sources import (
    SourceDeckRow,
    SourceRemovalView,
    SourceRow,
    SourceRunRow,
    SourceView,
)
from presentator.contracts.text import LobbyText
from presentator.host.config import Settings, WorkerSettings
from tests.gitmirror.test_mirror import DumbHttpHandler, RespondingHandler
from tests.presentator.host.test_worker_serving_loop import clients_closed_with_the_test
from tests.presentator.host.test_worker_settings import no_worker_variables

# `BaseHTTPRequestHandler`'s own request dispatch calls this by name; nothing
# in the test that stands the server up ever calls it itself.
DumbHttpHandler.log_message
RespondingHandler.log_message

# `HTMLParser.feed` dispatches to these by name; nothing calls them directly.
_InlineScripts.handle_starttag
_InlineScripts.handle_data
_InlineScripts.handle_endtag

# `presentator/api/templates/sources.html` renders each row of the Sources list.
SourceRow.name
SourceRow.url
SourceRow.access
SourceRow.state
SourceRow.state_word
SourceRow.fetched

# `presentator/api/templates/source.html` renders the source page.
SourceView.name
SourceView.url
SourceView.access
SourceView.state
SourceView.state_word
SourceView.fetched
SourceView.address
SourceView.webhook_secret
SourceView.webhook_secret_held_elsewhere
SourceView.carries_no_secret
SourceView.renews_deploy_key
SourceView.public_key
SourceView.runs
SourceView.decks
SourceRunRow.state
SourceRunRow.state_word
SourceRunRow.fetched
SourceRunRow.commit
SourceRunRow.reason
SourceDeckRow.slug
SourceDeckRow.title

# `presentator/api/templates/source_remove.html` renders the confirm page.
SourceRemovalView.name
SourceRemovalView.title
SourceRemovalView.body

# The words below reach a person through the Svelte Home page and deck page
# alone, which read them from `/api/session`; the drive proves the pages show
# them.
LobbyText.decks_column_changed
LobbyText.decks_empty_title
LobbyText.decks_empty_explanation
LobbyText.deck_back
LobbyText.deck_status
LobbyText.deck_state_ready
LobbyText.deck_state_building
LobbyText.deck_state_failed
LobbyText.deck_state_never_built
LobbyText.deck_built
LobbyText.deck_builds_with
LobbyText.deck_theme_separator
LobbyText.deck_not_built_explanation
LobbyText.deck_building_explanation
LobbyText.deck_building_label
LobbyText.deck_building_for
LobbyText.deck_failed_title
LobbyText.deck_attempt_label
LobbyText.deck_failed_without_a_message
LobbyText.deck_failed_output_megabytes
LobbyText.deck_failed_output_entries
LobbyText.deck_failed_output_shape
LobbyText.deck_failed_deck_megabytes
LobbyText.deck_failed_deck_paths
LobbyText.deck_failed_last_talk_stands
LobbyText.deck_presenter_view
LobbyText.deck_projector_view
LobbyText.deck_pdf
LobbyText.deck_unknown_explanation

# The words below reach a person through a Jinja template alone. That a catalog
# carries each of them is proven by `load_lobby_text`, which refuses a catalog
# missing any field of `LobbyText`; that a page renders them is proven by the
# route tests.
LobbyText.section_decks
LobbyText.decks_title
LobbyText.decks_column_deck
# The Svelte lobby's not-found board (F2b) and for-admins board (F4b) read these
# from `/api/session`.
LobbyText.address_unknown_title
LobbyText.address_unknown_explanation
LobbyText.for_admins_title
LobbyText.for_admins_explanation
LobbyText.settings_title
LobbyText.settings_instance_name
LobbyText.settings_default_language
LobbyText.settings_language_hint
LobbyText.settings_default_theme
LobbyText.settings_save
LobbyText.account_preferences
LobbyText.account_language
LobbyText.account_theme
LobbyText.sources_back
LobbyText.source_access
LobbyText.source_secret
LobbyText.source_secret_info_mark
LobbyText.source_secret_info
LobbyText.source_webhook_later
LobbyText.source_deploy_key_hint
LobbyText.source_webhook
LobbyText.source_webhook_address
LobbyText.source_webhook_secret
LobbyText.source_webhook_held_elsewhere
LobbyText.source_copy
LobbyText.source_webhook_payload
LobbyText.source_created_done
LobbyText.source_created
LobbyText.source_access_heading
LobbyText.source_webhook_heading
LobbyText.source_recent_runs
LobbyText.source_decks
LobbyText.source_reverse
LobbyText.source_push_inbox
LobbyText.source_later
LobbyText.source_renew
LobbyText.source_secret_dots
LobbyText.source_access_renew_info
LobbyText.source_deploy_key_info
LobbyText.source_public_key
LobbyText.source_deploy_key_renew
LobbyText.source_run_fetched
LobbyText.source_run_unreachable
LobbyText.source_run_secret
LobbyText.source_remove_heading
LobbyText.source_remove_hint
LobbyText.source_remove_button
LobbyText.source_remove_cancel
LobbyText.source_remove_confirm

# `StaticFiles.__call__` dispatches to this override for every talk request.
TalkFiles.get_response

# `StaticFiles.__call__` runs this no-op override instead of the base directory check.
TalkFiles.check_config

# Pydantic runs this `field_validator` on every `AskRequest.language` value.
AskRequest.empty_language_is_no_language

# pydantic reads a model's `model_config` by name when the class is built.
WorkerSettings.model_config

# Pydantic runs this `model_validator` after every `Settings` validation.
Settings._two_hostnames  # noqa: SLF001 - pydantic, not Python code, is the caller  # pyright: ignore[reportPrivateUsage]

# pytest applies this autouse fixture to every worker-settings test by itself.
no_worker_variables  # pyright: ignore[reportUnusedExpression]

# pytest applies this autouse fixture to every serving-loop test by itself.
clients_closed_with_the_test  # pyright: ignore[reportUnusedExpression]

# Pydantic reads these fields when it writes a validation answer.
ValidationFault.loc
ValidationFault.msg
