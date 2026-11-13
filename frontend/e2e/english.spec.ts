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

// The German source words that the English words differently, read from the generated words the
// pages render from; any of them on an English page is a page defect, also where the English
// word fell back to the German one. A German word that is also an English word of another key
// ("Status") can say nothing about a page and is left out. The match ignores case because the
// lobby uppercases headings, column headers and links, and innerText shows them so.
async function germanWordsDifferingFromEnglish(): Promise<RegExp[]> {
	const [german, english] = [await loadWords('de'), await loadWords('en')];
	const englishWords = new Set(
		Object.values(english).filter((word) => typeof word === 'string').map((word) => word.toLowerCase())
	);
	const differing = Object.entries(german).flatMap(([key, word]) =>
		typeof word === 'string' && word !== english[key as keyof Words] && !englishWords.has(word.toLowerCase())
			? [word]
			: []
	);
	return differing.map((word) => new RegExp(`(?<!\\p{L})${escaped(word)}(?!\\p{L})`, 'iu'));
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
	for (const german of await germanWordsDifferingFromEnglish()) {
		expect(shown, `a German word on the page: ${german}`).not.toMatch(german);
	}
	for (const key of Object.keys(words)) {
		expect(shown, `a catalog key on the page: ${key}`).not.toContain(key);
	}
	expect(shown).not.toMatch(/undefined|\[object Object\]/);
	expect(problems).toEqual([]);
}

// The signed-in surfaces: each names its address and the heading it shows.
const SIGNED_IN_SURFACES: Record<
	string,
	(words: Words, page: Page, slug: string) => Promise<[address: string, heading: string]>
> = {
	home: async (words) => ['/', words['decks.title']],
	deck: async (_words, _page, slug) => [`/deck/${slug}`, DECK_TITLE],
	settings: async (words) => ['/settings', words['settings.title']],
	sources: async (words) => [SOURCES_LIST, words['sources.title']],
	'new source': async (words) => [NEW_ADDRESS, words['sourceNew.title']],
	source: async (_words, page) => {
		const name = await ownSourceName(page, OWN_SOURCE);
		return [sourceAddress(name), name];
	}
};

test.describe('the signed-in surfaces in English', () => {
	test.use({ person: admin });

	for (const [surface, locate] of Object.entries(SIGNED_IN_SURFACES)) {
		test(`render ${surface}`, async ({ page, signedIn }) => {
			const words = await wordsOf(page);
			const problems = watchesErrors(page);
			await page.clock.setFixedTime(NOW);
			const [address, heading] = await locate(words, page, signedIn?.slugs[0] ?? '');

			await rendersInEnglish(
				page,
				problems,
				async () => {
					await page.goto(address);
					await expect(page.locator('main h1')).toHaveText(heading);
				},
				words
			);
		});
	}

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
	for (const address of ['/login', ...REFUSALS]) {
		test(`render ${address}`, async ({ page }) => {
			const words = await wordsOf(page);

			await rendersInEnglish(
				page,
				watchesErrors(page),
				async () => {
					await page.goto(address);
					await expect(page.locator('.signin .wordmark')).toHaveText(words['app.name']);
				},
				words
			);
		});
	}
});
