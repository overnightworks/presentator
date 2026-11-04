import { readDecks } from '#lib/api/decks.ts';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => ({ list: await readDecks(fetch) });
