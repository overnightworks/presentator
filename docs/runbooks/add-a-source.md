# Runbook: add a Git source

Symptom: a repository's decks should appear in presentator, and an instance
starts with no source. Impact: until a source exists, the lobby lists none of
that repository's decks.

## Check

Settings · Sources lists the source as reachable. A token the host turns down
shows as Refused, an answer of any other kind as Failed, and a host that never
answers (or a credential that cannot be read) as Error, with the reason under
the source's name. A private repository checked without a token also reads as
unreachable ([SR6](../sources.md)). Its decks appear in the list after the first fetch: the next
poll ([`PRESENTATOR_SOURCE_POLL_SECONDS`](../configuration.md)) or *Fetch now*
on the source's page.

## Fix

1. At the Git host, prepare the credential. For HTTPS, a public repository needs none; for a private one, create a
   read-only token. For SSH, you register the deploy key in step 3. Rollback: delete the token at
   the host.
2. Open Settings · Sources, choose *Add source*, and enter the name and the Git
   URL. The form shows what that address needs; there is no access to pick
   ([SR7](../sources.md)). Rollback: leave the page; nothing is stored yet.
3. HTTPS: the Secret field appears for an `https://` address; enter the token of a
   private repository there, because the address has no user part, and leave it empty
   for a public one. SSH: Add shows an owned ed25519 deploy-key draft; register its
   public half at the Git host. The first contact accepts the host's key and
   refuses the host if it ever changes.
   Rollback: delete the key at the host.

   HTTPS and SSH also show an optional Folder field: enter the part of the
   repository the decks live in, for example `examples`. It is set at Create and
   cannot be changed later.
4. Choose *Check connection*. It probes that exact token (or none) or draft before
   anything is stored, and *Create* stays disabled until it answers reachable.
   Rollback: none needed, nothing is stored.
5. Choose *Create*. The page shows the webhook secret once
   ([SR2](../sources.md); leaving or reloading the page loses it); store it if a Git host will call the
   [fetch-now hook](call-the-fetch-now-hook.md). Rollback: remove the source as
   [SR3](../sources.md) describes.

## Escalate

When the source stays Refused, Failed or Error after you renew its secret
([renew a source's secret](renew-a-source-secret.md)), ask the Git host's owner
whether the token or key still has read access to the repository.
