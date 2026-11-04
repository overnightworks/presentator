import type { Page } from '@playwright/test';

import type { SessionView } from '../src/lib/api/session.ts';
import { type DeckRecord, expect, signIn, test } from './person.ts';
import { sessionOf } from './scenario.ts';

async function slugsOfDecksOf(page: Page): Promise<string[]> {
	const answer = await page.request.get('/api/decks', {
		headers: { Accept: 'application/json' }
	});
	const { decks } = (await answer.json()) as { decks: { slug: string }[] };
	return decks.map((deck) => deck.slug);
}

test.describe('a member', () => {
	test.use({ person: { role: 'user' } });

	test('is no admin in the session', async ({ page }) => {
		const session = await sessionOf(page);

		expect(session.person?.role).toBe('user');
	});
});

const aDeck = (title: string): DeckRecord => ({
	state: 'ready',
	title,
	changed_at: '2026-11-03T12:00:00Z'
});

test.describe('a person with two decks', () => {
	test.use({ person: { role: 'user', decks: [aDeck('one'), aDeck('two')] } });

	test('sees their two decks', async ({ page }) => {
		expect(await slugsOfDecksOf(page)).toHaveLength(2);
	});
});

test.describe('a person without decks', () => {
	test.use({ person: { role: 'user' } });

	test('sees none', async ({ page }) => {
		expect(await slugsOfDecksOf(page)).toEqual([]);
	});
});

test.describe('a person who chooses a theme', () => {
	test.use({ person: { role: 'user' } });

	test("does not choose it for another test's person", async ({ page, browser, baseURL }) => {
		// A fetch from a page of the lobby, so the browser says it is same-origin.
		await page.goto('/login');
		const chosen = await page.evaluate(async () => {
			const answer = await fetch('/api/account', {
				method: 'PUT',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ theme: 'dark' })
			});
			return answer.ok;
		});
		expect(chosen).toBe(true);
		const other = await browser.newContext({ baseURL });
		await signIn(other, { role: 'user' });
		const theirs = (await (await other.request.get('/api/session')).json()) as SessionView;
		await other.close();

		expect((await sessionOf(page)).theme).toBe('dark');
		expect(theirs.theme).not.toBe('dark');
	});
});
