# Sign-in

Audience: whoever changes or operates the sign-in surface. This page holds the
sign-in surface's ruled sentences, each with the ID its test cites. Webauth's
[host contract](https://github.com/overnightworks/webauth/blob/main/docs/host-contract.md)
owns the session cookie and gate, what happens while Keycloak is down, which
refusal shows which page, the origin check and the signed-out redirect;
presentator holds only its own values here.

- **SI1** Only members of `/apps/presentator` get in, members of
  `/apps/presentator/admin` are admins, and only Keycloak decides who belongs
  where.
- **SI2** Presentator has no password form and no first-admin setup page, and it
  stores no login attempts. `/setup` answers 404, and the sign-in page has a
  single Keycloak button.
- **SI3** Signing out also ends the Keycloak session and lands on the sign-in
  page.
- **SI4** A sign-in lasts at most 12 hours, and the session cookie is issued
  once, at sign-in.
- **SI5** A session past its cap is deleted by the next sweep, and no session
  stores an IP address or a user agent.
- **SI6** The refresh token is stored in plain text for every member.
- **SI7** The lobby keeps the ID token in plain text in the session row, and the
  talk host stores none.
- **SI8** Opening the presenter or the projector signs in at the talk host
  through Keycloak, and a failed talk sign-in offers to start it again.
- **SI9** Signing out at the lobby ends the lobby and Keycloak sessions at once.
  The talk session ends when Keycloak reports the sign-out, otherwise after 12
  hours.
- **SI10** When Keycloak signs a person out, every presentator session of that
  person ends on both hosts. The route answers a `POST` under any Host but the
  talk host's, an internal service name or an address included, and the signed
  Logout Token is its only control; the talk host answers 404 there.
- **SI11** Only the fetch-now hook's prefix `/sources/` and
  `/auth/backchannel-logout` are exempt from the origin check.
- **SI14** Each host counts HTTP requests per address, behind the trusted proxy
  per forwarded client. The sign-in paths (start, return and, on the lobby,
  `/logout`) share 40 requests in ten minutes. On the talk host the built deck
  files under `/deck/` have 3000 a minute, because every load fetches each file
  again. Every other path has 600 a minute. A refusal is `429` with
  `Retry-After`. `/health` and the back-channel logout are exempt from every
  budget, and the co-presenter's WebSocket is not counted.
- **SI12** A refused sign-in lands on `/login/no-access` or `/login/login-failed`,
  which answer 403.
- **SI13** The legal links stand in the person menu and on the sign-in pages, and
  the lobby draws no footer. Design's stylesheet contract owns the placement for
  every app. This row owns it for both of presentator's pages, and the lobby
  page links here.
