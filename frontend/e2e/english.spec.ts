import type { Page } from '@playwright/test';

import { loadWords } from '../src/lib/words/index.ts';
import { type Person, expect, test } from './person.ts';
import { minutesAgo, NOW, wordsOf, type Words } from './scenario.ts';
import { OWN_SOURCE, ownSourceName, sourceAddress, SOURCES_LIST, NEW_ADDRESS } from './sources.ts';

// One render pass per lobby surface in a further language; the behaviour is
// proven by the suites that find their words by catalog key.
test.use({ locale: 'en-US' });

const DECK_TITLE = 'Agenten-Fabrik';
const admin: Person = {
	role: 'admin',
	source: OWN_SOURCE,
	decks: [{ state: 'ready', title: DECK_TITLE, changed_at: minutesAgo(180), built_at: minutesAgo(12) }]
};

const escaped = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// The German source words that the English catalog words differently.
async function germanWordsDifferingFromEnglish(english: Words): Promise<RegExp[]> {
	const german = await loadWords('de');
	return Object.entries(german)
		.filter(([key, word]) => typeof word === 'string' && word !== english[key as keyof Words])
		.map(([, word]) => new RegExp(`(?<!\\p{L})${escaped(word as string)}(?!\\p{L})`, 'u'));
}

// The refusal boards answer 403 by design, and the browser logs that for the document itself.
const REFUSALS = ['/login/login-failed', '/login/no-access'];

// What the page logs as an error or throws, from now on.
function watchesErrors(page: Page): string[] {
	const problems: string[] = [];
	page.on('console', (message) => {
		const refusal = REFUSALS.includes(new URL(message.location().url || 'about:blank').pathname);
		if (message.type() === 'error' && !refusal) problems.push(message.text());
	});
	page.on('pageerror', (error) => problems.push(error.message));
	return problems;
}

async function rendersInEnglish(
	page: Page,
	problems: string[],
	ready: () => Promise<void>,
	words: Words
) {
	await ready();

	await expect(page.locator('html')).toHaveAttribute('lang', 'en');
	const shown = await page.locator('body').innerText();
	for (const german of await germanWordsDifferingFromEnglish(words)) {
		expect(shown, `a German word on the page: ${german}`).not.toMatch(german);
	}
	for (const key of Object.keys(words)) {
		expect(shown, `a catalog key on the page: ${key}`).not.toContain(key);
	}
	expect(shown).not.toMatch(/undefined|\[object Object\]/);
	expect(problems).toEqual([]);
}

test.describe('the signed-in surfaces in English', () => {
	test.use({ person: admin });

	test('render home, deck, settings and its source pages', async ({ page, signedIn }) => {
		const words = await wordsOf(page);
		const problems = watchesErrors(page);
		await page.clock.setFixedTime(NOW);
		const heading = (text: string) => () => expect(page.locator('main h1')).toHaveText(text);
		const opens = (address: string, ready: () => Promise<void>) => async () => {
			await page.goto(address);
			await ready();
		};
		const name = await ownSourceName(page, OWN_SOURCE);
		const surfaces = [
			opens('/', heading(words['decks.title'])),
			opens(`/deck/${signedIn?.slugs[0]}`, heading(DECK_TITLE)),
			opens('/settings', heading(words['settings.title'])),
			opens(SOURCES_LIST, heading(words['sources.title'])),
			opens(NEW_ADDRESS, heading(words['sourceNew.title'])),
			opens(sourceAddress(name), heading(name))
		];
		for (const surface of surfaces) {
			await rendersInEnglish(page, problems, surface, words);
		}
	});

	test('render the not-found board', async ({ page }) => {
		const words = await wordsOf(page);

		await rendersInEnglish(
			page,
			watchesErrors(page),
			async () => {
				await page.goto('/nope');
				await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
			},
			words
		);
	});
});

test.describe('the sign-in boards in English', () => {
	test('render the sign-in, its failure and its refusal', async ({ page }) => {
		const words = await wordsOf(page);
		const problems = watchesErrors(page);
		const board = (address: string) => async () => {
			await page.goto(address);
			await expect(page.locator('.signin .wordmark')).toHaveText(words['app.name']);
		};

		for (const address of ['/login', ...REFUSALS]) {
			await rendersInEnglish(page, problems, board(address), words);
		}
	});
});
