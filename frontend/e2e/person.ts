import type { BrowserContext } from '@playwright/test';

import { expect, test as base } from './coverage.ts';

export interface Person {
	role: 'admin' | 'user';
	language_tag?: string | null;
	decks?: number;
}

// The one language besides English that the driven lobby speaks; it is built
// from en.po at serve time (tests/support/lobby_drive.py).
export const secondLanguage = 'eo';

const PERSON_ROUTE = '/drive/person';

type SessionCookies = Parameters<BrowserContext['addCookies']>[0];

/** Makes a new person in the driven lobby and signs this context in as them. */
export async function signIn(context: BrowserContext, person: Person): Promise<void> {
	const answer = await context.request.post(PERSON_ROUTE, { data: person });
	expect(answer.ok()).toBe(true);
	const { cookies } = (await answer.json()) as { cookies: SessionCookies };
	await context.addCookies(cookies);
}

// A test that names no person stays signed out; one that does gets its own
// account, so no test reads what another wrote.
export const test = base.extend<{ person: Person | undefined; signedIn: Person | undefined }>({
	person: [undefined, { option: true }],
	signedIn: [
		async ({ context, person }, use) => {
			if (person) {
				await signIn(context, person);
			}
			await use(person);
		},
		{ auto: true }
	]
});

export { expect };
