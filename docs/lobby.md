# Lobby

Audience: whoever changes or operates the lobby's header, theme and language.
This page holds the lobby's ruled sentences, each with the ID its test or check
cites. The legal links are [sign-in](sign-in.md) SI13, which owns them for both
pages. The catalog rule stands in [the architecture overview](ARCHITECTURE.md#5-crosscutting-concepts)
under "Words and look".

- **LB1** The admin sets the instance's language. A signed-in person whose
  account names a language presentator has a catalog for sees that language;
  everyone else sees the instance's.
- **LB2** The lobby has no account page. Each person chooses the theme in the
  person menu, the instance sets no theme, and whoever chooses none gets Follow
  system.
- **LB3** A theme or language change reaches the whole lobby at once, without a
  reload.
- **LB4** "Not found" and "for admins" show inside the lobby.
- **LB5** Browser storage holds only SvelteKit's navigation state in
  `sessionStorage`. The lobby keeps no app data, form input or secret there and
  uses no `localStorage`.
- **LB6** The content security policy admits exactly one inline style,
  SvelteKit's announcer style, by its sha256 with `'unsafe-hashes'`.
