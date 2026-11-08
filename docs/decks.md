# Decks

Audience: whoever writes a deck for this instance, or changes or operates the
decks surface. This page holds the deck author's folder contract and the decks
surface's ruled sentences, each with the ID its test cites. How a deck is
listed, built and delivered is in the code; the reasons for the sandbox and the
talk host's own origin are in [constraints and reasons](ARCHITECTURE.md#6-constraints-and-reasons).

## The deck folder

Authors outside this repository rely on these rules. Local assets,
`fonts.provider` and `favicon` are the steps of the runbook
[prepare a deck for the build](runbooks/prepare-a-deck-for-the-build.md).

- **DK1** A folder is a deck when it carries `slides.md` and a `deck.toml` that
  names a `title`.
- **DK2** The folder name is the deck's address, so a title change moves no
  link.
- **DK3** `language` is optional. `de` or `en` sets the language the
  co-presenter speaks, and any other value, or none, reads as German.
- **DK4** The build has no network and writes nothing outside the deck, so a
  deck builds only with the themes and addons `slidev/package.json` carries and
  with the files it carries itself.
- **DK5** The co-presenter is the one headmatter line
  `addons: [slidev-addon-presentator]`, and a deck copies none of its files.

## Who sees a deck

- **DK6** Only the account that owns a deck's source sees the deck in the list
  and opens its deck page, projector, presenter and PDF. No other admin does
  either.
- **DK7** A foreign deck answers like a deck that does not exist: the same
  status, body, relevant headers and redirects. The response time is not
  promised. Two answers show other decks on purpose: the count of decks that a
  source's removal names, and DK12.
- **DK8** An admin without decks sees an empty list, and its hint names no other
  admin's Git address.
- **DK9** A member without the admin role owns no source and so never sees a
  deck.
- **DK10** A deck always belongs to the owner of its source and never changes
  hands. Removing the source removes its decks for everyone, and a new owner
  arises only by creating the same address again.
- **DK11** Whoever falls out of the admin group keeps their decks and still sees
  and opens them: list, deck page, talk and PDF.
- **DK12** When two sources carry the same folder, the source that had it first
  keeps it, and the second source's deck appears for nobody, its owner
  included.
- **DK13** A talk runs on its own host, so deck code cannot reach the lobby: a
  session of one host is no session at the other. The host name stays in
  configuration.

## How long a deck and its build stay

- **DK14** The active build and PDF stay. A superseded build goes at the next
  delivery of its deck, and no build without an active deck stays on disk.
- **DK15** A deck whose folder is missing is deleted 30 days after the first
  refresh that missed it. Only a refresh that could read the source and still
  misses the folder deletes it. A folder still in the repository counts as
  there, even without a readable `deck.toml` or `slides.md`. An unreadable
  source deletes nothing, while an emptied branch or another repository behind
  the same source counts as read.
- **DK16** After the 30 days, the deck's row, build and PDF go. The source's
  mirror keeps the folder in its git history until the source is removed. A
  folder pushed again is a new deck at the same address, built at once. A
  renamed folder is a new deck at once, and its old address stops answering and
  goes after 30 days.
- **DK17** Until it is deleted, a marked deck holds its address against every
  other source. After that, another source with the same folder name takes the
  address at its next refresh at the latest, even another person's source, and
  old links then show its deck.
- **DK18** A source's decks are the folders directly under its folder, or
  directly under the repository's top when it names none. The folder's own name
  stays the deck's address.
