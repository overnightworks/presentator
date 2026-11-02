import { readSession } from '#lib/api/session.ts';
import type { LayoutLoad } from './$types';

export const load: LayoutLoad = async ({ fetch }) => ({ session: await readSession(fetch) });
