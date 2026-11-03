import { getJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type DeckListView = components['schemas']['DeckListView'];
export type DeckSummary = components['schemas']['DeckSummary'];
export type DeckState = components['schemas']['DeckState'];

export const DECK_LIST_PATH = '/api/decks';

export function readDecks(fetcher: typeof fetch): Promise<DeckListView> {
	return getJson<DeckListView>(fetcher, DECK_LIST_PATH);
}
