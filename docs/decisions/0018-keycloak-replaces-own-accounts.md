# ADR 0018: Keycloak replaces the accounts an admin creates and the instance's own login

Audience: humans and agents working on sign-in, on who may enter, or on who is
an admin.

- Status: ACCEPTED 2026-10-27 — supersedes the accounts an admin creates in
  [ADR 0011](0011-instance-users.md) and the own login of
  [ADR 0015](0015-public-like-songmaker-own-login.md), and only those two
  passages
- Date: 2026-10-27
- Decision authority: line c of
  [#242](https://github.com/overnightworks/presentator/issues/242); the
  operator's ruling of 2026-10-18 for the Keycloak sign-in; E7 of
  [#508](https://github.com/overnightworks/presentator/issues/508), ruled
  2026-10-27
- Neighbours: [ADR 0003](0003-libraries-for-models-and-auth.md) owns the login
  mechanics; the [sign-in reference](../reference/sign-in-and-session.md) owns
  what holds today

## Context

ADR 0011 decided that an admin creates accounts and that the first start of a
fresh instance creates the admin. ADR 0015 decided that the instance's own
login is the only door. Both made presentator the owner of passwords and
accounts, while the operator's other services already sign in through one
Keycloak.

## Decision

Keycloak owns accounts, sign-in and the two groups. Members of
`/apps/presentator` may enter; members of `/apps/presentator/admin` are admins.
Presentator keeps only the two roles `admin` and `user`, Settings without a
Users tab, and the session row. It stores no password and has no first-start
setup.

Everything else in ADR 0011 (the host owns the subscriptions, owners on decks
and sources, SQLite) and in ADR 0015 (public like Songmaker, no Cloudflare
Access) stands.

## Consequences

An account, a block and a group change are made in Keycloak. Until the
back-channel logout is registered, a block ends a session only at its cap
([runbook](../runbooks/configure-keycloak.md)).

## Rejected alternatives

Keeping the own accounts beside Keycloak: two sources of truth for who may
enter.
