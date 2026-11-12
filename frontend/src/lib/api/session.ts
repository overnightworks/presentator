import { getJson, signInStartsAt } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type SessionView = components['schemas']['SessionView'];
export type SessionPerson = components['schemas']['SessionPerson'];

export const SESSION_PATH = '/api/session';

/** Who reads, in which language and theme, and where sign-in starts. */
export async function readSession(fetcher: typeof fetch): Promise<SessionView> {
	const session = await getJson<SessionView>(fetcher, SESSION_PATH);
	signInStartsAt(session.sign_in.start);
	return session;
}
