# Runbook: start an instance in a container

Reader: the operator of this machine's deployment.

The instance is an edge app ([ADR 0019](../decisions/0019-presentator-is-an-edge-app.md)):
`hal apply` deploys `compose.yaml` from a `v*` tag, and that file carries every
value the deployment needs. Nothing is exported, no `.env` is written, and no
port is published; the edge's proxy is the only way in.

## Before you start

- A Keycloak client ([configure Keycloak](configure-keycloak.md)).
- The networks `hal_agent_presentator` and `hal_backchannel_presentator`, which
  the edge creates. While one is missing, `hal apply` fails on the missing
  external network and starts nothing.
- The two secret files compose mounts into the instance alone, read-only, at
  `/run/secrets`: the master key at `~/.secrets/presentator/secret-key` (a
  fresh random value of at least 32 characters) and the Keycloak client's
  secret at `~/.secrets/keycloak/clients/presentator`
  ([configure Keycloak](configure-keycloak.md)).
- A `v*` tag of this repository that holds the release.

## Steps

1. Run `hal apply` for the app `presentator`. The edge builds the image, starts
   the build worker and the instance, and waits for the instance's `/health`.
   The instance starts once the build worker answers, within 70 seconds of its
   start.
2. Open the public origin and sign in.

A deployed instance has no voice: the speech process is not part of the
release, so speaking and hearing report "not yet available"
([co-presenter and voice](../reference/co-presenter-and-voice.md)).

## Check

The instance trusts one proxy: the gateway `172.31.251.1` of its own network
`presentator_default`, which `compose.yaml` fixes. Without it the instance
answers every request against the connection it has: http, and one client
address for everyone. The form guard still passes a real browser, which sends
`Sec-Fetch-Site` regardless of the proxy.

Three named volumes hold what has to survive, `database`, `mirrors` and
`builds`, under the project `presentator`. A new tag through `hal apply` keeps
them: the next start finds the accounts, sources, decks and built talks, with
nothing built again. The upgrade is that new tag.
