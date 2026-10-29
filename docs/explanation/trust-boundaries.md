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

## The agent service holds the operator's Claude login

The co-presenter's answer comes from the operator's agent service, which holds
the Claude subscription and its card. The person's fresh access token and
Presentator's own workload token cross to it over the `hal_agent_presentator`
network, an internal network with no host address and no published port, whose
members the edge decides; the plain `http` hop is safe only because of that. The
question and the asked deck's words cross with them, and the model gets no
tools. The service checks the person's token on every ask; whom it admits is
edge's decision (`edge/docs/agent-service.md`). Speech stays a private Unix
socket under the shared UID: readiness, PCM, transcript and spoken sentences
cross it, and browser identity and credentials do not.

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
