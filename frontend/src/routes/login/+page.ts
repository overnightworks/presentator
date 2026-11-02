import { readSession } from '#lib/api/session.ts';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => ({ session: await readSession(fetch) });
