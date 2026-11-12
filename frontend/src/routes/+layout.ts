import { loadKitWords } from 'overnightworks-design';
import '#lib/languages.ts';
import { readSession } from '#lib/api/session.ts';
import { loadWords } from '#lib/words/index.ts';
import type { LayoutLoad } from './$types';

// The lobby is a single-page app: FastAPI serves its shell, so nothing renders
// on a server and nothing is prerendered.
export const ssr = false;
export const prerender = false;

// The session is read once here, and the words of its language beside it, the
// app's own and the kit's; every page below reads them from `data`. A change of
// the session's language reruns this load, so the new words reach the page
// without a reload.
export const load: LayoutLoad = async ({ fetch }) => {
	const session = await readSession(fetch);
	const [words, kitWords] = await Promise.all([
		loadWords(session.language),
		loadKitWords(session.language)
	]);
	return { session, words, kitWords };
};
