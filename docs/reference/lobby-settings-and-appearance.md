# Lobby settings and appearance reference

Audience: an engineer or operator looking up what the lobby's header,
Settings and theme do and how the lobby is themed.

## Header

Every signed-in page carries the picture's header
([mockups](../mockups/README.md)): the product name, the Decks section and
Settings for an admin. It also has one round person button, which opens appkit's
account menu in a native popover: the person's name opening the central account
in a new tab, the three theme rows, Log out, and the legal links; Escape and a
click outside close it. The header links to no lobby account page: there is
none, and `/account` is an unknown address that shows the not-found board. No page has a
footer: the Impressum and the Datenschutz links stand in this menu and on the sign-in pages.

## Settings

An admin opens Settings and sets the instance name, the default language and
the default theme. A person without the admin role is told inside the lobby
that Settings are for admins, and the header shows them no Settings link; the
API refuses the read and the write. Settings is one area with a tab strip. There
is no Users tab.

- General is a SvelteKit page under the settings layout, which draws the tab
  strip. It holds those instance defaults. Save writes all three at once and
  shows what is stored; the page repaints in the same document, because the
  session is read again, so a person's own theme still outranks the default. The instance name is
  kept and shown back there and read nowhere else.
- Sources lists each source with the state and age of its newest run and,
  under a failing one, the reason ([sources](sources.md)). Removing a source
  ends on the list with one notice. An instance with no source says what a
  source is and what you need for it.

## Theme

The language is the instance's: everybody reads the default an admin sets in
Settings. The theme is a person's own override, with the instance default
behind it. "Follow system" writes the value `follow-system` on the page, which
no theme rule matches, so the browser decides
([ADR 0012](../decisions/0012-themes-and-language.md)). A theme chosen in the
menu paints at once, with no reload, and is written by one
`PUT /api/account` with the theme alone, no `GET` first; if the write is
refused the radio snaps back. The instance defaults live in one SQLite row and
a person's theme in a row beside their account. A person cannot hand a chosen
theme back to the instance default in the menu; the API takes `null`.

## JSON

Settings and the theme are also readable and writable as JSON: `GET` and `PUT`
`/api/settings` (admin only, else a 403 problem) and `/api/account` (every
signed-in person, the theme alone). A write replaces what the page writes and
answers with what is now stored; on Settings a newly chosen language no
catalog speaks is a 422 problem and nothing is written, and the language
already stored is accepted back. The Settings read carries the language list
(`tag` and the catalog's own `name`), so no page holds one of its own
([ADR 0020](../decisions/0020-the-lobby-is-a-json-api.md)).

## Language

English is the only catalog the repository ships. Every catalog file the
catalog directory holds is offered as a language, so a second language is a
file and not a change to code, and each catalog names itself.

## Look

The lobby's tokens, fonts and components come from appkit's npm tarball, pinned
in `frontend/package.json`, and presentator fills only appkit's app slot with
its teal ([ADR 0012](../decisions/0012-themes-and-language.md)). No stylesheet
or script is vendored in this repository.
