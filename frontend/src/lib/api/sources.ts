import { postJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type AccessKind = components['schemas']['AccessKind'];
export type ConnectionCheckView = components['schemas']['ConnectionCheckView'];
export type CreatedSourceView = components['schemas']['CreatedSourceView'];
export type KeyDraftView = components['schemas']['KeyDraftView'];
export type NewSource = components['schemas']['NewSource'];
export type SourceProbe = components['schemas']['SourceProbe'];

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
