# ADR 0019: Presentator is an edge app, deployed by `hal apply` from a tag

Audience: humans and agents working on `compose.yaml`, on how an instance is
reached or deployed, or on what a deployed instance can do.

- Status: ACCEPTED 2026-10-27 — amends [ADR 0007](0007-browser-client-behind-tunnel.md)
  (the tunnel on this host and the published port become the edge's proxy and
  catalog) and [ADR 0017](0017-a-build-worker-runs-each-step-sealed.md) (the
  worker restarts `unless-stopped`; the rest of its seal stands)
- Date: 2026-10-27
- Decision authority: the operator's ruling of 2026-10-26 that every app works
  like hopin; line 4 of
  [#508](https://github.com/overnightworks/presentator/issues/508), ruled
  2026-10-26; the edge's answers of 2026-10-27 in
  [overnightworks/edge#431](https://github.com/overnightworks/edge/issues/431)
- Neighbours: [ADR 0015](0015-public-like-songmaker-own-login.md) and
  [ADR 0018](0018-keycloak-replaces-own-accounts.md) keep the instance's own
  sign-in

## Context

The instance ran from `docker compose up` on one machine: a published port on
`127.0.0.1`, a tunnel on the host, a `.env` the operator wrote, the host speech
socket bound into the container. The edge deploys every other app of the
operator from a tag with `hal apply`, and its contract refuses a file that
publishes a port, reads an `env_file`, binds a host path or restarts `always`.

## Decision

`compose.yaml` is the deployment, and the edge reads it at a `v*` tag.

- The instance is reached only through the edge's proxy, by the labels on the
  web service; the talk host is the same service under `edge.alias`. The file
  publishes no port and binds nothing outside the release: named volumes,
  tmpfs and file secrets only.
- Plain values stand in the file as literals. The edge runs Compose with the
  caller's whole environment, so a default form would let a shell decide the
  live origins. Secrets are the two file secrets from `~/.secrets`.
- The instance has its own network and subnet `172.31.251.0/24`; the trusted
  proxy is that network's gateway. It also joins the edge's back-channel
  network, where Keycloak reaches the logout route.
- The web has a healthcheck on `/health`. The worker keeps ADR 0017's seal,
  including no healthcheck, and restarts `unless-stopped`: the edge counts a
  service without a healthcheck once it is running.
- The host speech process is not part of the release. A deployed instance has
  no voice and reports it as not yet available.

## Consequences

- Upgrading is a new tag through `hal apply`. The runbooks for upgrading, for
  backing up (the SQLite backup is the edge's), for starting over from an old
  database and for a folder on this box are retired.
- The worker ends after every step and Docker restarts it, so the edge's one
  look may meet `restarting`; its health wait polls and the next look finds
  `running`.
- The edge's checker is the one enforcement point of its contract; CI proves
  the facts of this file and that the whole file starts.
