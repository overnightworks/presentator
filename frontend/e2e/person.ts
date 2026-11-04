import type { BrowserContext } from '@playwright/test';

import type { FailureView } from '../src/lib/api/decks.ts';
import { expect, test as base } from './coverage.ts';

export interface AttemptRecord {
	commit?: string;
	started_at?: string;
	// The reason is the wire's own type; a reason names only the counts it needs.
	failure?: Partial<FailureView> & Pick<FailureView, 'reason'>;
}

// A ready deck has a build made at `built_at` (the drive's own instant unless
// given); a building or failed one has a build only when `built_at` is given,
// and an attempt (a default one unless given). The harness refuses an attempt
// on a ready or never-built deck, and a missing PDF without a build.
export interface DeckRecord {
	state: 'ready' | 'building' | 'failed' | 'never-built';
	title: string;
	changed_at: string;
	built_at?: string;
	attempt?: AttemptRecord;
	pdf?: 'missing';
}

export interface Person {
	role: 'admin' | 'user';
	// The one language besides English that the driven lobby speaks, by name:
	// tests/support/lobby_drive.py owns its tag.
	language?: 'second';
	decks?: DeckRecord[];
	// The address of a source this person owns.
	source?: string;
}

const PERSON_ROUTE = '/drive/person';

type SessionCookies = Parameters<BrowserContext['addCookies']>[0];

/** Makes a new person in the driven lobby and signs this context in as them. */
export async function signIn(
	context: BrowserContext,
	person: Person
): Promise<{ personId: string; slugs: string[] }> {
	const answer = await context.request.post(PERSON_ROUTE, { data: person });
	expect(answer.ok()).toBe(true);
	const { cookies, person_id, slugs } = (await answer.json()) as {
		cookies: SessionCookies;
		person_id: string;
		slugs: string[];
	};
	await context.addCookies(cookies);
	return { personId: person_id, slugs };
}

/** Adds a deck record to a person the driven lobby already made; answers its slug. */
export async function addDeck(
	context: BrowserContext,
	personId: string,
	record: DeckRecord
): Promise<string> {
	const answer = await context.request.post(`${PERSON_ROUTE}/${personId}/deck`, { data: record });
	expect(answer.ok()).toBe(true);
	return ((await answer.json()) as { slug: string }).slug;
}

// A test that names no person stays signed out; one that does gets its own
// account, so no test reads what another wrote.
export const test = base.extend<{
	person: Person | undefined;
	signedIn: { personId: string; slugs: string[] } | undefined;
}>({
	person: [undefined, { option: true }],
	signedIn: [
		async ({ context, person }, use) => {
			await use(person ? await signIn(context, person) : undefined);
		},
		{ auto: true }
	]
});

export { expect };
