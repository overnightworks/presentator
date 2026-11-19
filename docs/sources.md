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
  token is for https only, and a key address never sends one. Only https and
  ssh addresses are sources; `file://` and a bare path are refused like any
  unknown address. Either kind also offers an optional folder.
- **SR8** A source may name a folder of its repository, written with `/`, no
  leading slash, no `..`, no empty part, each part at most 255 bytes. A
  malformed one is refused, and a trailing `/` or surrounding space is dropped.
  The folder is set at Create and not editable, and the source's page names
  it. Check connection does not look at it, and a folder that is missing or is
  a file reads as an emptied branch (DK15). One address stays one source, so a
  second source on the same address with another folder is a duplicate address.
- **SR10** A source button shows that its request runs and, after it ends,
  how it ended.
- **SR11** One process of the instance takes sources in, builds and removes at a
  time. A fetch that meets another process's refresh does nothing and answers
  without a run. A removal waits for that refresh as long as a build step may
  take, then fails like any other error, deletes nothing, and a retry removes.
  A source taken in within the poll interval waits for the next tick.
- **SR12** A key address trusts the host key the instance first met for its
  host, at Check connection or a pull in any process, and every process
  refuses a host that answers with another key: the check and every pull name
  the host-key refusal until `presentator unpin-host` removes that host's pin
  ([runbook](runbooks/unpin-a-host-key.md)).
- **SR13** A source's mirror is a cache on each process's own disk, rebuilt
  from the source by its next pull there. A removal deletes the mirror on the
  process that runs it; a mirror of the removed source on another process's
  disk goes at that process's next source poll tick. A removal that leaves a
  mirror or build directory on disk, or that the store refuses, deletes no row
  and answers 500 with a problem whose `reason` is `disk-leftover` or
  `store-unavailable`, with no `leftover` key.
