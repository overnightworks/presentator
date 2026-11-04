# Lobby settings and appearance reference

Audience: an engineer or operator looking up what the lobby's header,
Settings and Account do and how the lobby is themed.

## Header

Every signed-in page carries the picture's header
([mockups](../mockups/README.md)): the product name, the Decks section and
Settings for an admin. On the SvelteKit pages it also has one
round person button, which opens appkit's account menu in a native popover: the
person's name opening the central account in a new tab, the three theme rows,
Log out, and the legal links; Escape and a click outside close it. The Svelte
header has no way to Account yet. A retained Jinja page's header shows the
username as a details control instead, whose menu holds a link to the local
account page, the three theme rows and Log out, without legal links. The footer carries the Impressum and
the Datenschutz links.

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

## Account

Everybody opens Account and overrides language and theme for themselves alone.
Both resolve the same way: the person's own choice first, the instance default
behind it. "Follow system" writes no `data-theme` attribute on a Jinja page,
and the value `follow-system` on a SvelteKit page, which no theme rule
matches; either way the browser decides
([ADR 0012](../decisions/0012-themes-and-language.md)). A theme chosen in the
SvelteKit menu paints at once, with no reload, and is written by `GET` then
`PUT /api/account` with the stored language override, because the write
replaces both; if the write is refused the radio snaps back. The
instance defaults live in one SQLite row, a person's overrides in a row beside
their account, and a theme chosen in the person menu is that same Account
preference. Account carries neither a password nor a sessions part.

## JSON

Settings and Account are also readable and writable as JSON: `GET` and `PUT`
`/api/settings` (admin only, else a 403 problem) and `/api/account` (every
signed-in person). A write replaces what the page writes and answers with what
is now stored; a newly chosen language no catalog speaks is a 422 problem and
nothing is written; the language already stored is accepted back. Both reads
carry the language list (`tag` and the catalog's own `name`), so no page holds
one of its own
([ADR 0020](../decisions/0020-the-lobby-is-a-json-api.md)).

## Language

English is the only catalog the repository ships. Every catalog file the
catalog directory holds is offered as a language, so a second language is a
file and not a change to code, and each catalog names itself.

## Look

The lobby's colours come from the vendored
[`overnightworks/ui-console`](https://github.com/overnightworks/ui-console)
v0.2.0 pin; Pico leftover chrome lives in `lobby.css`. Settings and Account use
this product's Pico markup, not the package grammar.

htmx 2.x is vendored beside Pico and carries the two writes a person makes
about themselves: the theme rows of the person menu and the Account
preferences post, and the page is painted again in what they chose.
jinja2-fragments ([ADR 0008](../decisions/0008-lobby-server-rendered.md)) is
not installed, because no surface here has a partial to serve: both writes
change the whole document, down to the `<html>` element the theme sits on.
