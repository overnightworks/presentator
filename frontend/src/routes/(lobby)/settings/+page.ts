import { readSettings } from '#lib/api/settings.ts';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, url }) => ({
	settings: await readSettings(fetch, url)
});
