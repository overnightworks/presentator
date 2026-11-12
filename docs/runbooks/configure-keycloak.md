# Runbook: configure Keycloak for sign-in

Reader: the administrator of the Keycloak realm.

There is no password login and no `/setup`: [sign-in](../sign-in.md) (SI2).

## Before you start

The instance's `PRESENTATOR_PUBLIC_ORIGIN` and `PRESENTATOR_TALK_ORIGIN`
(both https) are known, and so is the name under which Keycloak reaches the
instance inside the internal network, because step 4 registers the back-channel
logout URL under that name.

## Steps

1. Create a client for the instance. Its id is
   `PRESENTATOR_OIDC_CLIENT_ID`; write its secret to a file whose absolute path
   is `PRESENTATOR_OIDC_CLIENT_SECRET_FILE`. The file is read once at start,
   minus one trailing newline.
2. Set `PRESENTATOR_OIDC_ISSUER` exactly to the `issuer` of the realm's
   discovery document, without a trailing slash. The token's `iss` is compared
   with it character for character: nothing is normalised and an explicit port
   stays part of it.
3. Turn `full.path` on in the client's groups mapper. With bare group names
   nobody matches `/apps/presentator` and every sign-in shows "No access".
   Members of `/apps/presentator` are admitted; members of
   `/apps/presentator/admin` are admins.
4. Register `http://<name>/auth/backchannel-logout` as the client's
   back-channel logout URL, where `<name>` is the name under which Keycloak
   reaches the instance inside the network, in plain HTTP: TLS ends before the
   instance. The route answers a `POST` under any Host and port except the talk
   host's, which never serves it, so a Service name works as well; the signed
   Logout Token is its only control, and the deployment keeps the path off the
   public internet. The route is not live until the client registers it; a valid
   Logout Token then ends every session of its subject, on every device. Until
   it is registered, a block in Keycloak ends a session only at its 12 h cap.
5. Register `<PRESENTATOR_PUBLIC_ORIGIN>/login` as the post-logout redirect
   URI, or Keycloak shows an error page at every logout.
6. Register both `<PRESENTATOR_PUBLIC_ORIGIN>/auth/callback` and
   `<PRESENTATOR_TALK_ORIGIN>/auth/callback` as valid redirect URIs: the talk
   host signs in at its own.

## Let an agent act as a person

The lobby always serves `/mcp` and its discovery document; the edge's `/mcp`
line decides whether the internet reaches them. A person's agent reaches the
person's account through `<PRESENTATOR_PUBLIC_ORIGIN>/mcp`, as that person: it
lists their decks, sees whether each is built and why a build failed, reads
their text, reads who is signed in and the account's theme, and sets that
theme. It never deletes, shares, publishes or sends anything out, and never
acts as an admin: the settings, the sources and the people routes stay closed
to its token. Each route's tool or exclusion is declared beside the route. The
realm names below are fixed in the app; no setting changes them.

1. Use the shared `agents` client: one public client for every app, with
   client authentication off (no secret) and PKCE S256. Its id is
   `agents`; it is no entry of `PRESENTATOR_TOKEN_CLIENTS`.
2. Create one optional client scope for this app, named `mcp-presentator`.
   Give it an audience mapper whose included custom audience is
   `<PRESENTATOR_PUBLIC_ORIGIN>/mcp`, so a token requested with this scope has
   that as its only audience. Put no audience on the client itself: the other
   apps' fronts use the same client.
3. Assign the scope to the `agents` client as an optional scope.
4. Create the group `/apps/presentator/mcp` for the people whose agents may
   call. A token needs this group
   besides the app's own.
5. Keep the realm's agent group `/agents`. A service account in it, with the
   app's groups and scope on its token, signs in at `/mcp` with its own client
   as an agent; the app creates its account on that first call. It sees no
   deck: decks belong to people. Its token carries the `agent_owner` and
   `agent_name` claims, which the realm must set on it.
6. Leave the realm's access token lifetime at 300 seconds: the app adds none.

For a person's agent, the person must have signed in once in a browser: a token
of a person's subject the app has no account for is refused, and no account is
made from it.

## Check

Sign in: a member of the group lands on the lobby, a non-member sees "No
access". Log out: the browser returns to `/login`. An INFO log line records an
account's creation or role change (event kind, Keycloak subject id, role).
