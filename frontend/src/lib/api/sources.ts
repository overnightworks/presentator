import { Declined, getJson, postJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type AccessRenewal = components['schemas']['AccessRenewal'];
export type AccessKind = components['schemas']['AccessKind'];
export type ConnectionCheckView = components['schemas']['ConnectionCheckView'];
export type CreatedSourceView = components['schemas']['CreatedSourceView'];
export type KeyDraftView = components['schemas']['KeyDraftView'];
export type NewSource = components['schemas']['NewSource'];
export type SourceProbe = components['schemas']['SourceProbe'];
export type SourceRunFailure = components['schemas']['SourceRunFailure'];
export type SourceRunView = components['schemas']['SourceRunView'];
export type SourceState = components['schemas']['SourceState'];
export type SourceView = components['schemas']['SourceView'];
export type WebhookSecretView = components['schemas']['WebhookSecretView'];

// `new` is the name no source can have, so the address is never a source's own.
const NEW_SOURCE_PATH = '/api/sources/new';
const SOURCES_PATH = '/api/sources';

type Here = Pick<Location, 'pathname' | 'search'>;

/** The admin's draft deploy key; asking again before it is used answers the same one. */
export function openKeyDraft(fetcher: typeof fetch, here: Here): Promise<KeyDraftView> {
	return postJson<KeyDraftView>(fetcher, `${NEW_SOURCE_PATH}/key-draft`, undefined, here);
}

/** Probes the address; a reachable answer carries the fingerprint a create needs. */
export function checkConnection(
	fetcher: typeof fetch,
	probe: SourceProbe
): Promise<ConnectionCheckView> {
	return postJson<ConnectionCheckView, SourceProbe>(fetcher, `${NEW_SOURCE_PATH}/check`, probe);
}

/** Stores a checked source; the answer is the one place its webhook secret is shown. */
export function createSource(fetcher: typeof fetch, source: NewSource): Promise<CreatedSourceView> {
	return postJson<CreatedSourceView, NewSource>(fetcher, SOURCES_PATH, source);
}

/** The address of one source's JSON, which a page re-reads by invalidating it. */
export const sourcePath = (name: string): string => `${SOURCES_PATH}/${encodeURIComponent(name)}`;

/** One source as its page shows it; the answer carries no secret. */
export function readSource(fetcher: typeof fetch, name: string, here: Here): Promise<SourceView> {
	return getJson<SourceView>(fetcher, sourcePath(name), here);
}

/** Refreshes that one source now. */
export function fetchNow(fetcher: typeof fetch, name: string): Promise<void> {
	return postJson<undefined>(fetcher, `${sourcePath(name)}/fetch`, undefined);
}

/**
 * Renews the access secret, or the deploy key when `secret` is null. A source
 * that took nothing answers "declined"; any other failure propagates.
 */
export async function renewAccess(
	fetcher: typeof fetch,
	name: string,
	secret: string | null
): Promise<'declined' | undefined> {
	try {
		await postJson<undefined, AccessRenewal>(fetcher, `${sourcePath(name)}/access`, { secret });
	} catch (failure) {
		if (failure instanceof Declined) {
			return 'declined';
		}
		throw failure;
	}
}

/** Mints a new webhook secret; the answer is the one place it is shown. */
export function renewWebhook(fetcher: typeof fetch, name: string): Promise<WebhookSecretView> {
	return postJson<WebhookSecretView>(fetcher, `${sourcePath(name)}/webhook-secret`, undefined);
}
