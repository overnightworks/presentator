"""Names that something other than a Python call site reaches.

A Svelte page is not Python, so a field only a page reads looks unused; a
standard-library base class's own dispatch is invisible the same
way. Every entry names the site that reaches it; an entry without one is an
excuse.
"""

from presentator.api.copresenter import AskRequest
from presentator.api.decks import TalkFiles
from presentator.api.lobby_shell import _InlineScripts  # pyright: ignore[reportPrivateUsage] - HTMLParser.feed, not Python code, calls its hooks
from presentator.api.problems import ValidationFault
from presentator.contracts.text import LobbyText
from presentator.host.config import Settings, WorkerSettings
from tests.gitmirror.test_mirror import DumbHttpHandler, RespondingHandler
from tests.presentator.host.test_worker_serving_loop import clients_closed_with_the_test
from tests.presentator.host.test_worker_settings import no_worker_variables
from tests.support.lobby_drive import DeckRecord

# `BaseHTTPRequestHandler`'s own request dispatch calls this by name; nothing
# in the test that stands the server up ever calls it itself.
DumbHttpHandler.log_message
RespondingHandler.log_message

# `HTMLParser.feed` dispatches to these by name; nothing calls them directly.
_InlineScripts.handle_starttag
_InlineScripts.handle_data
_InlineScripts.handle_endtag

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
LobbyText.decks_title
LobbyText.decks_column_deck

# The words below reach a person through the Svelte Sources list, Add source
# page, source page and removal dialog alone, which read them from
# `/api/session`; the drive proves the pages show them.
LobbyText.source_secret_info
LobbyText.source_kind_pending
LobbyText.source_kind_failed
LobbyText.source_kind_retry
LobbyText.source_webhook_later
LobbyText.source_deploy_key_hint
LobbyText.source_webhook
LobbyText.source_webhook_address
LobbyText.source_webhook_payload
LobbyText.source_created_done
LobbyText.source_created
LobbyText.source_name
LobbyText.source_url
LobbyText.source_folder_optional
LobbyText.source_folder
LobbyText.source_check
LobbyText.source_create
LobbyText.source_deploy_key_heading
LobbyText.source_webhook_once
LobbyText.source_created_toast
LobbyText.source_check_reachable
LobbyText.source_refused_name
LobbyText.source_refused_folder
LobbyText.source_refused_duplicate_name
LobbyText.source_refused_duplicate_url
LobbyText.source_refused_unknown_access
LobbyText.source_refused_not_checked
LobbyText.source_refused_secret_not_allowed
LobbyText.source_refused_outside_mount
LobbyText.source_refused_deploy_key
LobbyText.source_secret
LobbyText.source_webhook_secret
LobbyText.source_copy
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
LobbyText.source_refused_secret
LobbyText.source_run_refused
LobbyText.source_run_failed
LobbyText.source_run_column_state
LobbyText.source_run_column_fetched
LobbyText.source_run_column_detail
LobbyText.decks_column_state
LobbyText.settings_sources
LobbyText.sources_column_source
LobbyText.sources_column_fetched
LobbyText.sources_fetch_now
LobbyText.source_busy_check
LobbyText.source_busy_create
LobbyText.source_busy_fetch
LobbyText.source_busy_renew
LobbyText.source_busy_count
LobbyText.source_busy_remove
LobbyText.source_state_reachable
LobbyText.source_state_error
LobbyText.source_state_refused
LobbyText.source_state_failed
LobbyText.source_state_never_fetched
LobbyText.sources_empty_title
LobbyText.sources_empty_explanation
LobbyText.sources_empty_need
LobbyText.sources_empty_ssh
LobbyText.sources_empty_or
LobbyText.sources_empty_https
LobbyText.sources_add
LobbyText.sources_column_access
LobbyText.source_access_token
LobbyText.source_access_deploy_key
LobbyText.source_access_local
LobbyText.source_remove_confirm_title
LobbyText.source_remove_confirm_body
LobbyText.source_remove_deck_singular
LobbyText.source_remove_deck_plural
LobbyText.source_remove_talk_singular
LobbyText.source_remove_talk_plural
LobbyText.source_remove_run_singular
LobbyText.source_remove_run_plural
LobbyText.source_remove_refused
LobbyText.source_removed
LobbyText.sources_back
LobbyText.source_remove_cancel
LobbyText.source_remove_confirm

# The words below reach a person through a Svelte page. That a catalog carries
# each of them is proven by `load_lobby_text`, which refuses a catalog missing
# any field of `LobbyText`; that a page renders them is proven by the drive.
LobbyText.section_decks
LobbyText.section_settings
# The kit's account menu and sign-in pages read these through `kitWords.ts`.
LobbyText.theme_title
LobbyText.theme_follow_system
LobbyText.theme_light
LobbyText.theme_dark
LobbyText.sign_in_logged_out
LobbyText.sign_in_log_in
LobbyText.sign_in_failed
LobbyText.sign_in_failed_banner
LobbyText.sign_in_try_again
LobbyText.sign_in_no_access
LobbyText.sign_in_no_access_sentence
LobbyText.legal_impressum
LobbyText.legal_datenschutz
LobbyText.legal_nutzungsbedingungen
# The Svelte lobby's not-found board, for-admins board and Settings page read
# these from `/api/session`, and the person menu its round button.
LobbyText.address_unknown_title
LobbyText.address_unknown_explanation
LobbyText.for_admins_title
LobbyText.for_admins_explanation
LobbyText.menu_account
LobbyText.settings_title
LobbyText.settings_instance_name
LobbyText.settings_default_language
LobbyText.settings_language_hint
LobbyText.settings_save
LobbyText.sources_back
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

# Pydantic calls the drive's deck-record validator when a request is parsed.
DeckRecord.builds_fit_the_state
