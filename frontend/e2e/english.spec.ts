import { readFileSync } from 'node:fs';
import { join } from 'node:path';

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

// The English catalog's own pairs: the German source word and its English word.
const ENGLISH_CATALOG = readFileSync(join(import.meta.dirname, '../src/lib/catalogs/en.po'), 'utf8');
const ENGLISH_PAIRS = [...ENGLISH_CATALOG.matchAll(/^msgid "(.+)"\nmsgstr "(.+)"$/gm)].map(
	([, german = '', english = '']) => ({ german, english })
);

// The German source words the English catalog words differently; any of them on an English
// page is a page defect, also where the generated English word fell back to the German one.
function germanWordsDifferingFromEnglish(): RegExp[] {
	return ENGLISH_PAIRS.filter(({ german, english }) => german !== english).map(
		({ german }) => new RegExp(`(?<!\\p{L})${escaped(german)}(?!\\p{L})`, 'u')
	);
}

// Every German key has an English word, and none is blank.
async function expectsEveryKeyWorded(english: Words): Promise<void> {
	const german = await loadWords('de');
	for (const key of Object.keys(german) as (keyof Words)[]) {
		const word = english[key];
		expect(word, `no English word for ${key}`).toBeDefined();
		if (typeof word === 'string') expect(word.trim(), `a blank English word for ${key}`).not.toBe('');
	}
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
	await expectsEveryKeyWorded(words);
	await ready();

	await expect(page.locator('html')).toHaveAttribute('lang', 'en');
	const shown = await page.locator('body').innerText();
	for (const german of germanWordsDifferingFromEnglish()) {
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
