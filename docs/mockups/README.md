# Mockups

Audience: anyone ruling or building a surface; the picture owner for the lobby.

This directory holds the picture source for a surface change and the PNGs
rendered from it for review. `m0-lobby.html` is the source of truth — a single
HTML sheet of artboards, one `<figure class="board">` per screen state; the
PNGs in `m0-lobby/` and the overview `m0-lobby-sheet.png` are derived renders
committed alongside it so a reader never has to open the HTML or an external
artifact link to see the picture. This sheet is the picture owner for the M0 lobby; its content was blessed on
06.09.2026 (issue #8) and its look is the family look (issue #610). A change to a
surface starts here: extend this sheet, get the blessing on the owning item,
re-render, then rule.

The Keycloak sign-in picture (signed out, No access, Login failed, person menu,
Account; no first-start page, local login form, Users, password change, or
session list) was blessed by the operator on 19.10.2026 (K13, issue #242).

## M0 lobby sheet

Source: `m0-lobby.html`. Overview: `m0-lobby-sheet.png` (the whole sheet, light, at
half size). The lobby boards are in the family look of appkit's
[`docs/family.html`](https://github.com/overnightworks/appkit/blob/a161c7c/docs/family.html)
with presentator's teal in the app slot (`#0c6c77` on `#ffffff` in light,
`#56c3cd` on `#06181b` in dark). Each lobby board is drawn once and shown at 1280 px
and at 390 px side by side; the page theme follows the device until the control at
the top of the sheet chooses. Per-board renders are in `m0-lobby/`, device scale
factor 2, cropped to each lobby `.board`: `<id>.png` in light and `<id>-dark.png` in
dark, where `<id>` is the figure's `id`. The room and presenter boards are deck
surfaces and keep their own renders. "Rulings" names the
[#185](https://github.com/overnightworks/presentator/issues/185) sentences and
rulings, the Voice requirements or the K13 sign-in picture each board shows; "new
words" marks a string that is a proposal because the catalog has none.

### How the sheet loads the look

`m0-lobby.html` links `appkit/appkit.css`, the unmodified `appkit.css` and fonts of
appkit v0.6.0 (the wheel's `appkit/static/`), so the sheet draws with appkit's own
tokens and fonts, never a copy of its values. On top of it the sheet carries
its own component styles for the boards (bar, tabs, rows, fields, cards, menu) and
presentator's colour in the app slot; `appkit.css` itself stays as shipped.

The room and presenter groups (class `deck`) keep the look their frozen pictures
(#139, #293 S12) were drawn in: their tokens, board chrome and IBM Plex from Google
Fonts are scoped to `.deck`, so their renders do not change with the lobby's look.

### Rendering

`node docs/mockups/render.mjs` writes every lobby PNG and the overview;
`node docs/mockups/render.mjs <id> ...` writes only those boards. It resolves
Playwright through the declared `playwright-chromium` of `slidev/`, so run
`pnpm --dir slidev install --frozen-lockfile` first.

### Sign-in

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/login-redirect.png`, `login-redirect-dark.png` | Not signed in: straight to the central sign-in | sentence 2 |
| `m0-lobby/login.png`, `login-dark.png` | Signed out | Picture · family §3, sentence 2 |
| `m0-lobby/no-access.png`, `no-access-dark.png` | No access | E7, sentence 7 |
| `m0-lobby/login-failed.png`, `login-failed-dark.png` | Login failed | E7, sentence 7 |

### Decks

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/deck-list-empty.png`, `deck-list-empty-dark.png` | Home · first day, empty | Picture · states |
| `m0-lobby/deck-list.png`, `deck-list-dark.png` | Home · the deck list | Picture · states, sentence 9 |
| `m0-lobby/person-menu.png`, `person-menu-dark.png` | Person menu open | Picture · family §4 |

### Settings · General

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/settings-general.png`, `settings-general-dark.png` | General | Picture · controls, sentence 3 (E5), sentence 9 |

### Settings · Sources

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/sources.png`, `sources-dark.png` | Sources · three sources | Picture · states, sentence 9 (#349) |
| `m0-lobby/sources-empty.png`, `sources-empty-dark.png` | Sources · first day, empty | Picture · states |
| `m0-lobby/sources-add.png`, `sources-add-dark.png` | Add source | Picture · controls, sentence 9 |
| `m0-lobby/sources-check-reachable.png`, `sources-check-reachable-dark.png` | Add source · check: reachable | Picture · states |
| `m0-lobby/sources-check-auth-failed.png`, `sources-check-auth-failed-dark.png` | Add source · check: refused | Picture · states |
| `m0-lobby/sources-check-unreachable.png`, `sources-check-unreachable-dark.png` | Add source · check: unreachable | Picture · states |
| `m0-lobby/sources-created.png`, `sources-created-dark.png` | Source created · secret shown once | E1, Picture · states |
| `m0-lobby/sources-detail.png`, `sources-detail-dark.png` | Source · HTTPS token | sentence 9, E1 |
| `m0-lobby/sources-detail-ssh.png`, `sources-detail-ssh-dark.png` | Source · SSH deploy key | sentence 9, E1 |
| `m0-lobby/sources-renew-access.png`, `sources-renew-access-dark.png` | Source · renew access | sentence 9, E1 |
| `m0-lobby/sources-renew-webhook.png`, `sources-renew-webhook-dark.png` | Source · renew webhook | E1 |
| `m0-lobby/sources-remove.png`, `sources-remove-dark.png` | Source · remove, confirm | Picture · new board |

### Settings · Voice

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/voice.png`, `voice-dark.png` | Voice · resting | V1–V9, Picture · states |
| `m0-lobby/voice-downloading.png`, `voice-downloading-dark.png` | Voice · downloading | V1, V9 |
| `m0-lobby/voice-loading.png`, `voice-loading-dark.png` | Voice · loading | V2, V3, V9 |
| `m0-lobby/voice-failed-active.png`, `voice-failed-active-dark.png` | Voice · load failed, previous voice stays active | V4, V9 |
| `m0-lobby/voice-failed-no-active.png`, `voice-failed-no-active-dark.png` | Voice · load failed, no active voice | V4, V5, V9 |
| `m0-lobby/voice-service-unknown.png`, `voice-service-unknown-dark.png` | Voice · service status unknown | V9 |
| `m0-lobby/voice-sample-loading.png`, `voice-sample-loading-dark.png` | Voice · sample loading | V8, V9 |
| `m0-lobby/voice-sample-playing.png`, `voice-sample-playing-dark.png` | Voice · sample playing | V8, V9 |
| `m0-lobby/voice-sample-error.png`, `voice-sample-error-dark.png` | Voice · playback failed | V8, V9 |

### Account

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/account.png`, `account-dark.png` | Account | Picture · family §4 |

### Deck page

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/deck-page-ready.png`, `deck-page-ready-dark.png` | Deck page · ready | Picture · states |
| `m0-lobby/deck-page-building.png`, `deck-page-building-dark.png` | Deck page · building | Picture · states |
| `m0-lobby/deck-page-failed.png`, `deck-page-failed-dark.png` | Deck page · failed | Picture · states |

### Not found and for admins

| PNG | Caption | Rulings |
|---|---|---|
| `m0-lobby/not-found.png`, `not-found-dark.png` | Unknown deck, inside the lobby (catalog words) | E6, sentence 5 |
| `m0-lobby/not-found-address.png`, `not-found-address-dark.png` | Unknown address, inside the lobby | E6, sentence 5, new words |
| `m0-lobby/for-admins.png`, `for-admins-dark.png` | Admin address opened by a non-admin | E6, sentence 5, new words |

Every signed-in board keeps the footer with Impressum and Datenschutz as #196
ruled; the sign-in boards carry the same two links below the card. The person menu
additionally carries the legal row, as family §4 has it: that row is an addition,
and whether the footer stays beside it is the operator's choice. Both link to the
central pages on `hallucinai.de` (Datenschutz at its `#presentator` section); the
Datenschutz page itself is edge's, with presentator's words in
`legal/datenschutz.html`, so no board draws it (issue #593).

M0 has no Start: the deck page offers presenter view, projector view, and the PDF.
Start belongs to the co-presenter run in M1. Instance language and theme live under
Settings (admin); a personal override lives on Account, and theme in the bar lives
in the person menu.

The Settings · Voice picture was blessed by the operator on 10.09.2026 (issue
#123, parent #108). Its V1–V9 behavior contract is owned by
[`docs/requirements/0001-voice-models.md`](../requirements/0001-voice-models.md);
the Voice boards remain surface evidence, not an implementation claim. Piper and
Chatterbox are installed on the live instance; Qwen3-TTS 0.6B, VoxCPM2, and NVIDIA
Magpie are proposed candidates only, and their not-downloaded state depicts intended
future behaviour. No benchmark, percentage, size, or cost is shown for any candidate.

### Presenter Home (proposal · issue #139)

The proposed pictures extend Slidev’s public navigation-controls hook, whose
proposed source owner is `examples/copresenter-deck/custom-nav-controls.vue`.
They do not change the existing Voice picture or claim implementation. A
home-outline Home pill returns to the homepage with all talks; Home is its
accessible name and tooltip. Home and the one CoPresenter instance share the
presenter toolbar layout: its 44 px AI toggle is in flow while inactive, and
its bounded active conversation body grows above that toggle. Home is absent
from projector view.

| PNG | Caption |
|---|---|
| `m0-lobby/presenter-return.png` | Presenter, AI off, Home visible — 1024 px |
| `m0-lobby/presenter-return-narrow.png` | Presenter, focused Home — 390 px |
| `m0-lobby/presenter-home-active.png` | Presenter, AI active — 1024 px |
| `m0-lobby/presenter-home-active-narrow.png` | Presenter, AI active — 390 px |
| `m0-lobby/projector-return-absent.png` | Projector — Home absent, 1024 px |

### The room knows (issue #525, #293 S12)

The notice that the room hears an AI, in the form the operator ruled on #293
line 6 (28.10.2026): at switch-on, until the AI's first answer, the whole
sentence of #293's E8 (ending in the pointer to the Datenschutzerklärung) stands
large at the bottom of the slide; afterwards, while the AI is on, only a small
pill in the top-right corner with a pulsing dot and „KI hört zu“ / „KI
antwortet“. Top-right covers the least of a typical slide: footers, page
numbers, chart axes and source lines sit at the bottom edge. Both follow the
deck's language, never shrink the slide, take no clicks (`pointer-events: none`)
and are never a control. The presenter's current-slide preview is the projector
board scaled down whole, with the same proportions, and shows what the
room sees; the AI's first answer begins at once, with no introduction. The
boards show the rulings of 28.10.2026 on #293: line 1 C (no introduction), 2 A
(the deck's language), 3 A (only while listening or answering), 5 A (the neutral
sentence) and line 6 (banner, then pill). The Lines column names the #293 S12
lines each board shows, not only those its caption cites.

| PNG | Caption | Lines |
|---|---|---|
| `m0-lobby/room-off.png` | Projector, AI off: no label, 1024 px | 3 |
| `m0-lobby/room-not-hearing.png` | Projector, AI on but not hearing: no banner, no pill, presenter window shows the failure, 1024 px | 3 |
| `m0-lobby/room-banner.png` | Projector, switch-on banner with the whole sentence, before the first answer, 1024 px | 2, 6 |
| `m0-lobby/room-banner-dark.png` | Projector, switch-on banner, dark theme, 1024 px | 2, 6 |
| `m0-lobby/room-banner-1920.png` | Projector, switch-on banner, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-banner-1920-dark.png` | Projector, switch-on banner, dark theme, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-banner-english.png` | Projector, English deck: switch-on banner with the English sentence, 1024 px | 2, 6 |
| `m0-lobby/room-listening.png` | Projector, AI listening after the first answer: corner pill, 1024 px | 2, 6 |
| `m0-lobby/room-listening-dark.png` | Projector, AI listening, dark theme, 1024 px | 2, 6 |
| `m0-lobby/room-listening-1920.png` | Projector, AI listening, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-listening-1920-dark.png` | Projector, AI listening, dark theme, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-listening-english.png` | Projector, English deck listening: pill „AI is listening“, 1024 px | 2, 6 |
| `m0-lobby/room-answering.png` | Projector, AI answering: pill with the speak glyph, 1024 px | 2, 6 |
| `m0-lobby/room-answering-dark.png` | Projector, AI answering, dark theme, 1024 px | 2, 6 |
| `m0-lobby/room-answering-1920.png` | Projector, AI answering, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-answering-1920-dark.png` | Projector, AI answering, dark theme, 1920 px (shown at half size) | 2, 6 |
| `m0-lobby/room-busy.png` | Projector, pill leaves the footer and the chart axis free, 1024 px | 6 |
| `m0-lobby/room-busy-dark.png` | Projector, busy slide, dark theme, 1024 px | 6 |
| `m0-lobby/room-busy-1920.png` | Projector, busy slide, 1920 px (shown at half size) | 6 |
| `m0-lobby/room-busy-1920-dark.png` | Projector, busy slide, dark theme, 1920 px (shown at half size) | 6 |
| `m0-lobby/room-presenter-listening.png` | Presenter, before the first answer: the preview is the projector board scaled down whole, banner included, 1024 px | 2, 6 |
| `m0-lobby/room-presenter-answering.png` | Presenter, AI answering: the preview is the projector board scaled down whole, pill included, the answer begins at once, 1024 px | 1, 6 |
| `m0-lobby/room-presenter-listening-narrow.png` | Presenter, before the first answer: the scaled projector board with the banner, 390 px | 2, 6 |
| `m0-lobby/room-presenter-answering-narrow.png` | Presenter, AI answering: the scaled projector board with the pill, 390 px | 1, 6 |
