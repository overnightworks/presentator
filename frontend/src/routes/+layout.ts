import { readSession } from '#lib/api/session.ts';
import type { LayoutLoad } from './$types';

// The lobby is a single-page app: FastAPI serves its shell, so nothing renders
// on a server and nothing is prerendered (ADR 0021).
export const ssr = false;
export const prerender = false;

// The session is read once here; every page below reads it from `data`.
export const load: LayoutLoad = async ({ fetch }) => ({ session: await readSession(fetch) });
