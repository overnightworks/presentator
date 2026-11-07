# Runbook: renew a source's webhook secret

Symptom: the webhook secret leaked, or was lost. Impact: anyone holding it can
start a fetch of that source.

## Check

Call the hook with the new secret as [call the fetch-now hook](call-the-fetch-now-hook.md)
describes: the source's page shows a new run. The old secret answers `404`.

## Fix

1. Open `/settings/sources/<name>` as an admin and choose the webhook's own
   Renew. The new value is shown on the page that renewed it until you leave it.
2. Set the new value at the Git host's webhook settings.

There is no rollback: the old secret stops working at once ([SR2](../sources.md)),
so the caller gets `404` until it holds the new value. Renew again to mint
another.

## Escalate

When the new secret still gets `404`, follow the hook's
[escalation](call-the-fetch-now-hook.md#escalate) and ask the Git host's owner.
