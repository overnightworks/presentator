import type { BrowserContext } from '@playwright/test';

import { expect, test as base } from './coverage.ts';

export interface DeckRecord {
	state: 'ready' | 'building' | 'failed' | 'never-built';
	title: string;
	changed_at: string;
	slug?: string;
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
export async function signIn(context: BrowserContext, person: Person): Promise<string> {
	const answer = await context.request.post(PERSON_ROUTE, { data: person });
	expect(answer.ok()).toBe(true);
	const { cookies, person_id } = (await answer.json()) as {
		cookies: SessionCookies;
		person_id: string;
	};
	await context.addCookies(cookies);
	return person_id;
}

/** Adds a deck record to a person the driven lobby already made. */
export async function addDeck(
	context: BrowserContext,
	personId: string,
	record: DeckRecord
): Promise<void> {
	const answer = await context.request.post(`${PERSON_ROUTE}/${personId}/deck`, { data: record });
	expect(answer.ok()).toBe(true);
}

// A test that names no person stays signed out; one that does gets its own
// account, so no test reads what another wrote.
export const test = base.extend<{
	person: Person | undefined;
	signedIn: { personId: string } | undefined;
}>({
	person: [undefined, { option: true }],
	signedIn: [
		async ({ context, person }, use) => {
			await use(person ? { personId: await signIn(context, person) } : undefined);
		},
		{ auto: true }
	]
});

export { expect };
