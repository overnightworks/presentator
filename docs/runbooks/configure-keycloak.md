# Runbook: configure Keycloak for sign-in

Reader: the administrator of the Keycloak realm.

There is no password login and no `/setup`: [sign-in](../sign-in.md) (SI2).

## Before you start

The instance's `PRESENTATOR_PUBLIC_ORIGIN` and `PRESENTATOR_TALK_ORIGIN`
(both https) are known. Inside the internal network the lobby's public
hostname must resolve to the instance (an alias), because the instance answers
only its two configured hostnames.

## Steps

1. Create a client for the instance. Its id is
   `PRESENTATOR_OIDC_CLIENT_ID`; write its secret to a file whose absolute path
   is `PRESENTATOR_OIDC_CLIENT_SECRET_FILE`. Under compose the file on the host
   is `~/.secrets/keycloak/clients/presentator`, which compose mounts, and the
   container reads it at `/run/secrets/oidc_client_secret`. The file is read
   once at start, minus one trailing newline.
2. Set `PRESENTATOR_OIDC_ISSUER` exactly to the `issuer` of the realm's
   discovery document, without a trailing slash. The token's `iss` is compared
   with it character for character: nothing is normalised and an explicit port
   stays part of it.
3. Turn `full.path` on in the client's groups mapper. With bare group names
   nobody matches `/apps/presentator` and every sign-in shows "No access".
   Members of `/apps/presentator` are admitted; members of
   `/apps/presentator/admin` are admins.
4. Register `http://presentator.hallucinai.de:8000/auth/backchannel-logout` as
   the client's back-channel logout URL. Keycloak reaches it inside the
   network `hal_backchannel_presentator` through the alias
   `presentator.hallucinai.de`, in plain HTTP: TLS ends at the edge. Its
   hostname is the lobby's public hostname, and the port does not matter to the
   match; a request under any other Host, an internal hostname or an IP
   included, gets 404 and ends no session. The route is not live until the
   client registers it; a valid Logout Token then ends every session of its
   subject, on every device. The web joins
   `hal_backchannel_presentator` with this release; registering the client's
   URL is the edge's (overnightworks/edge#446). Until it is registered, a block
   in Keycloak ends a session only at its 12 h cap.
5. Register `<PRESENTATOR_PUBLIC_ORIGIN>/login` as the post-logout redirect
   URI, or Keycloak shows an error page at every logout.
6. Register both `<PRESENTATOR_PUBLIC_ORIGIN>/auth/callback` and
   `<PRESENTATOR_TALK_ORIGIN>/auth/callback` as valid redirect URIs: the talk
   host signs in at its own.

## Let an agent list a person's decks

Optional: skip it and the instance serves no `/mcp`. The agent reaches the
person's decks through `<PRESENTATOR_PUBLIC_ORIGIN>/mcp`, as that person, and
nothing else.

1. Use the shared `agents` client: one public client for every app, with
   client authentication off (no secret) and PKCE S256. Its id is
   `PRESENTATOR_AGENT__MCP__CLIENT_ID`; it is no entry of
   `PRESENTATOR_TOKEN_CLIENTS`.
2. Create one optional client scope for this app, in the style of
   `mcp-presentator`; its name is `PRESENTATOR_AGENT__MCP__SCOPE`. Give it an
   audience mapper whose included custom audience is
   `<PRESENTATOR_PUBLIC_ORIGIN>/mcp`, so a token requested with this scope has
   that as its only audience. Put no audience on the client itself: the other
   apps' fronts use the same client.
3. Assign the scope to the `agents` client as an optional scope.
4. Create a group below `/apps/presentator` for the people whose agents may
   call; its path is `PRESENTATOR_AGENT__MCP__GROUP`. A token needs this group
   besides the app's own.
5. Name the realm's agent group, `/agents`, in
   `PRESENTATOR_AGENT__MCP__AGENT_GROUP`. A service account in it, with the
   app's groups and scope on its token, signs in at `/mcp` with its own client
   as an agent; the app creates its account on that first call. It sees no
   deck: decks belong to people.
6. Leave the realm's access token lifetime at 300 seconds: the app adds none.

A person must have signed in once in a browser: a token of a subject the app
has no account for is refused, and no account is made from it.

## Check

Sign in: a member of the group lands on the lobby, a non-member sees "No
access". Log out: the browser returns to `/login`. An INFO log line records an
account's creation or role change (event kind, Keycloak subject id, role).
