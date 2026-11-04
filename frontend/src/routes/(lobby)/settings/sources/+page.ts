import { readSources } from '#lib/api/sources.ts';
import type { PageLoad } from './$types';

// A 403 becomes the for-admins board and a 401 names this load's own address.
export const load: PageLoad = async ({ fetch, url }) => ({
	sources: await readSources(fetch, url)
});
