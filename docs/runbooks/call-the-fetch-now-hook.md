# Runbook: call the fetch-now hook from a Git host

Symptom: a push should start a fetch at once instead of at the next poll.
Impact: until the Git host calls the hook, new decks wait for the poll.

The Git host's webhook (or a `post-receive` hook) sends `POST` to the hook
address the Sources page shows for the source, with the source's webhook
secret as `Authorization: Bearer <secret>` or `X-Gitlab-Token: <secret>`. No
payload is read, so any host can call it. Why no login stands in front of it is
in [the architecture](../ARCHITECTURE.md).

## Check

A push shows a new run on the source's page, and the hook answers `204`. A wrong address (a trailing slash
or another path under `/sources/` included), an unknown source, a wrong secret
and a missing secret all answer `404` with an empty body. Only `POST` is open on
the hook address; every other method there leads to the login.

## Fix

1. Have the source's webhook secret. It is shown once, when the source is
   created or its secret renewed ([SR2](../sources.md)), because presentator
   keeps only its SHA-256 hash; a lost one is
   [renewed](renew-a-webhook-secret.md). Rollback: none, this step changes
   nothing.
2. In the Git host's webhook settings, add a webhook with the hook address and
   the secret. Rollback: delete the webhook at the Git host.
3. Push to the repository and run the Check. Rollback: none; the push is the
   repository's own change and this step adds nothing else.

## Escalate

When a push still starts no run and the address answers `404` with the right
secret, [renew the webhook secret](renew-a-webhook-secret.md) and set it again.
If that fails, ask the Git host's owner whether the webhook can reach the
instance's address.
