import { readSource } from '#lib/api/sources.ts';
import type { PageLoad } from './$types';

// The answer carries no secret; a 404 and a 403 become the root error page's
// boards, and a 401 names this load's own address.
export const load: PageLoad = async ({ fetch, params, url }) => ({
	source: await readSource(fetch, params.name, url)
});
