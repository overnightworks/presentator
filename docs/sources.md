# Sources

Audience: whoever changes or operates sources. This page holds the sources
surface's ruled sentences, each with the ID its test cites. The operator's facts
about sources stand in [configuration](configuration.md) and the runbook to
[add a Git source](runbooks/add-a-source.md).

- **SR1** An allowed source address names exactly one host and carries no
  secret, checked in one place.
- **SR2** The webhook secret comes back once, in the create or renew answer.
  Nothing keeps it, and a lost one is renewed.
- **SR3** Removing a source is confirmed in a dialog on its page, and the admin
  lands on the list with a short notice.
- **SR4** Sources stay shared among admins, and a source's page lists only the
  viewing admin's own decks.
- **SR5** Only admins create sources.
- **SR6** An https address may carry no secret, and a private repository
  without one fails Check connection as unreachable.
- **SR7** The address decides how a source is read: one parser on the server
  names the access, and the Add form shows only what that address needs. The
  token is for https only, and a key or folder address never sends one. A key
  or web address also offers an optional folder, a server folder none.
- **SR8** A source may name a folder of its repository, written with `/`, no
  leading slash, no `..`, no empty part, each part at most 255 bytes. A
  malformed one is refused, and a trailing `/` or surrounding space is dropped.
  The folder is set at Create and not editable, and the source's page names
  it. Check connection does not look at it, and a folder that is missing or is
  a file reads as an emptied branch (DK15). One address stays one source, so a second source on the same address
  with another folder is a duplicate address.
- **SR9** The folder column is additive, so an older release still reads and
  writes the table, but it reads a stored folder as the top of the repository.
  Its next refresh then marks that source's nested decks removed, and a
  same-named top-level deck can take the row. Before rolling back across this
  release, remove each source that carries a folder: removal deletes its builds,
  deck rows, run history and the source, so its decks are rebuilt, and adding it
  again needs folder-aware code and issues a new webhook secret that must be
  entered at the git host again. A restored older backup is migrated at its next
  start.
