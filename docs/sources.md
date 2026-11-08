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
