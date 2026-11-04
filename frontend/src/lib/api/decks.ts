import { getJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type DeckListView = components['schemas']['DeckListView'];
export type DeckSummary = components['schemas']['DeckSummary'];
export type DeckView = components['schemas']['DeckView'];
export type FailureView = components['schemas']['FailureView'];
export type DeckState = components['schemas']['DeckState'];

export const DECK_LIST_PATH = '/api/decks';

export function readDecks(fetcher: typeof fetch, here: URL): Promise<DeckListView> {
	return getJson<DeckListView>(fetcher, DECK_LIST_PATH, here);
}

/** One deck's facts; a deck that is unknown or another person's is the 404 the transport raises. */
export function readDeck(fetcher: typeof fetch, slug: string, here: URL): Promise<DeckView> {
	return getJson<DeckView>(fetcher, `${DECK_LIST_PATH}/${encodeURIComponent(slug)}`, here);
}
