# About trust boundaries

Audience: an engineer or operator asking why the instance guards what it
guards, and what each guard costs.

## A deck is code

A deck's Vue components and global layers run in its build, so the author of a
deck can run code. An instance therefore builds every deck in the build worker
([ADR 0005](../decisions/0005-deck-folder-and-slidev.md); the exact bounds are
[builds](../reference/builds.md)). `compose.yaml` hands every step to the
worker over a socket in a volume the two services share. The worker is the one
service that runs a deck, sealed
([ADR 0017](../decisions/0017-a-build-worker-runs-each-step-sealed.md)): no
network, every capability dropped, none of the server's environment, only the
deck's tree and its own output directory. The web holds no Docker socket, no
docker group and no Docker client, and runs with every capability dropped, no
new privilege and a read-only root, so a deck's code reaches neither the daemon
nor this machine's root.

A machine whose decks are all the operator's own can trade the sandbox away with
`PRESENTATOR_BUILD_RUNNER=host`, which runs every deck's code as the server,
with its rights over every file and the network. An instance serving a
repository somebody else can push to does not.

## A source on this box is trusted as the operator

A folder under the local-sources mount is read with the operator's trust: its
repositories, their metadata (a `gitdir` file, `objects/info/alternates`, a
symlink) and everyone who can write there. It is the door for the operator's
own folder, not for a repository somebody else controls.

## The gateway holds the operator's model account

The co-presenter's answer comes from the operator's LiteLLM gateway, which holds
the provider account and its budgets. Presentator holds its own gateway key, a
file mounted into the instance alone, and sends it as the bearer over the
`hal_agent_presentator` network, an internal network with no host address and no
published port, whose members the edge decides; the plain `http` hop is safe
only because of that. The question and the asked deck's words cross with it, and
the model gets no tools. The person crosses as a fingerprint of their issuer and
account id; no token of theirs and no name does. Whom the gateway admits is
hal's; Presentator answers only members of its API group, read from the issuer
for every question. Speech is a shared service on the internal network
`hal_speech_presentator`, with no connection to the internet. Both tokens cross
to it, the person's access token and Presentator's own, on every speak request
and Realtime upgrade; so do the sentences, the person's audio and the
transcripts. Nothing of the browser's identity does, and the service keeps
neither tokens, audio nor text.

## Keycloak reaches the logout route from inside

Keycloak calls the web's port 8000 for back-channel logout over the internal
network `hal_backchannel_presentator`, under the alias
`presentator.hallucinai.de`, without passing the edge's proxy
([ADR 0019](../decisions/0019-presentator-is-an-edge-app.md)). It is not the
trusted proxy, so the instance takes its address from the connection and
ignores any `X-Forwarded-For` it sends.

## The webhook has a secret, not a login

A git host has no browser to sign in with, so the sessionless
`POST /sources/<name>/fetch` is guarded by a per-source secret and reads no
payload ([ADR 0015](../decisions/0015-public-like-songmaker-own-login.md)).

## The admin routes take a token, not a login

`/api/admin/` is for hal, the operator's tool, and takes an access token of
Keycloak alone: a session cookie counts for nothing there, so a stolen or
forged browser session cannot erase a person. Which clients may call it is
configuration, and an instance that names none serves no admin route at all.
The server keeps no access log, because the path of such a call carries the
person's Keycloak subject. See [people](../reference/people.md).

A person's own token on the rest of `/api/` is that person, with exactly their
rights: the account behind the token is admitted as a sign-in would admit it,
and no session exists to steal. The admin prefix stays a machine path.
