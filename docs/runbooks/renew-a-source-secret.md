# Runbook: renew a source's secret

Symptom: a source's Git access token or deploy key expired, or is unreadable,
for example after a restore without the master key file. Impact: that source's
fetch fails and its decks stay listed but get no updates. Symptom: the token
or key leaked. Impact: whoever holds it can read the repository, while the
fetch still works. The webhook secret has its own
[runbook](renew-a-webhook-secret.md).

## Check

On the source's page choose *Fetch now*: the source's state reads reachable.

## Fix

1. Open `/settings/sources/<name>`. Rollback: none, this step changes nothing.
2. Renew by the access kind. HTTPS token: choose *Renew secret* and enter the
   new value; nothing is shown back. SSH deploy key: choose *Renew*; it
   replaces the local encrypted pair at once and shows the replacement public
   key, which you register at the Git host before the next fetch (renewal
   neither contacts that host nor keeps an old-key overlap). Rollback: none,
   the old value is overwritten; issue a new token or key at the Git host and
   renew again.
3. Revoke a leaked credential at the Git host; do not keep it valid. Rollback:
   none, issue a new credential there if the revoked one was still needed.

## Escalate

When the source stays Refused, Failed or Error, check the key file setting
([`PRESENTATOR_SECRET_KEY_FILE`](../configuration.md)), which also says what an
unreadable secret means. Then ask the Git host's owner whether the credential
has read access.
