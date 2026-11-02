import { getJson } from '#lib/api/client.ts';
import type { components } from '#lib/api/types.ts';

export type SessionView = components['schemas']['SessionView'];
export type LobbyText = components['schemas']['LobbyText'];

export const SESSION_PATH = '/api/session';

/** Who reads, in which words and theme, and where sign-in starts. */
export function readSession(fetcher: typeof fetch): Promise<SessionView> {
	return getJson<SessionView>(fetcher, SESSION_PATH);
}
