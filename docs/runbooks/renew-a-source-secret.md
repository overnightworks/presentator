# Runbook: renew a source's secret

Symptom: a source's Git access token or deploy key leaked or expired, or it is
unreadable, for example after a restore without the master key file. Impact:
that source's fetch fails and its decks stay listed but get no updates. The
webhook secret has its own [runbook](renew-a-webhook-secret.md).

## Check

On the source's page choose *Fetch now*: the source's state reads reachable.

## Fix

1. Open `/settings/sources/<name>`.
2. HTTPS token: choose *Renew secret* and enter the new value. Nothing is shown back.
3. SSH deploy key: choose *Renew*. It replaces the local encrypted pair at once
   and shows the replacement public key. Register it at the Git host before the
   next fetch; renewal neither contacts that host nor keeps an old-key overlap.

There is no rollback: the old value is overwritten. If the Check still fails,
issue a new token or key at the Git host and renew again. Revoke a leaked
credential there; do not keep it valid.

## Escalate

When the source stays refused, check the key file setting
([`PRESENTATOR_SECRET_KEY_FILE`](../configuration.md)), which also says what an
unreadable secret means. Then ask the Git host's owner whether the credential
has read access.
