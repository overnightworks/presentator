import { isHttpError } from '@sveltejs/kit';
import { readDeck } from '#lib/api/decks.ts';
import type { PageLoad } from './$types';

// A deck that is unknown or another person's answers 404, and this page draws
// its own board for that; every other failure stays the root error page's.
export const load: PageLoad = async ({ fetch, params, url }) => {
	try {
		return { deck: await readDeck(fetch, params.slug, url) };
	} catch (error_) {
		if (isHttpError(error_, 404)) {
			return { deck: null };
		}
		throw error_;
	}
};
