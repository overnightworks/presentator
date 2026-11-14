import type { Page } from '@playwright/test';

import { type Person, expect, test } from './person.ts';
import { minutesAgo, NOW, wordsOf, type Words } from './scenario.ts';
import { OWN_SOURCE, ownSourceName, sourceAddress, SOURCES_LIST, NEW_ADDRESS } from './sources.ts';

// The render pass: every lobby surface in each catalog language at a phone and a desktop width;
// the behaviour is proven by the suites that find their words by catalog key.
const LANGUAGES = [
	{ language: 'de', locale: 'de-DE' },
	{ language: 'en', locale: 'en-US' }
];
const WIDTHS = [
	{ name: 'mobile', viewport: { width: 390, height: 844 } },
	{ name: 'desktop', viewport: { width: 1280, height: 800 } }
];

const DECK_TITLE = 'Agenten-Fabrik';
const admin: Person = {
	role: 'admin',
	source: OWN_SOURCE,
	decks: [{ state: 'ready', title: DECK_TITLE, changed_at: minutesAgo(180), built_at: minutesAgo(12) }]
};

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

// The visible text elements whose text is cut off or spills out, unless they scroll by design.
function overflowingTextElements(): string[] {
	const scrollsByDesign = new Set(['auto', 'scroll']);
	const overflowing: string[] = [];
	for (const element of document.body.querySelectorAll('*')) {
		const hasText = [...element.childNodes].some(
			(node) => node.nodeType === Node.TEXT_NODE && (node.textContent ?? '').trim() !== ''
		);
		const style = getComputedStyle(element);
		const shown = element.getClientRects().length > 0 && style.visibility === 'visible';
		if (!hasText || !shown || element.clientWidth === 0) continue;
		if (element.scrollWidth > element.clientWidth && !scrollsByDesign.has(style.overflowX)) {
			const classes = [...element.classList].map((name) => `.${name}`).join('');
			overflowing.push(
				`${element.tagName.toLowerCase()}${classes}: ${element.scrollWidth} > ${element.clientWidth}`
			);
		}
	}
	return overflowing;
}

async function rendersWell(
	page: Page,
	language: string,
	problems: string[],
	ready: () => Promise<void>,
	words: Words
) {
	await ready();

	await expect(page.locator('html')).toHaveAttribute('lang', language);
	const shown = await page.locator('body').innerText();
	for (const key of Object.keys(words)) {
		expect(shown, `a catalog key on the page: ${key}`).not.toContain(key);
	}
	expect(shown).not.toMatch(/undefined|\[object Object\]|=>|\bfunction\s*\(/);
	expect(await page.evaluate(overflowingTextElements), 'text wider than its box').toEqual([]);
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

for (const { language, locale } of LANGUAGES) {
	for (const { name: widthName, viewport } of WIDTHS) {
		test.describe(`in ${language} at the ${widthName} width`, () => {
			test.use({ locale, viewport });

			test.describe('the signed-in surfaces', () => {
				test.use({ person: admin });

				for (const [surface, locate] of Object.entries(SIGNED_IN_SURFACES)) {
					test(`render ${surface}`, async ({ page, signedIn }) => {
						const words = await wordsOf(page);
						const problems = watchesErrors(page);
						await page.clock.setFixedTime(NOW);
						const [address, heading] = await locate(words, page, signedIn?.slugs[0] ?? '');

						await rendersWell(
							page,
							language,
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

					await rendersWell(
						page,
						language,
						watchesErrors(page),
						async () => {
							await page.goto('/nope');
							await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
						},
						words
					);
				});
			});

			test.describe('the sign-in boards', () => {
				for (const address of ['/login', ...REFUSALS]) {
					test(`render ${address}`, async ({ page }) => {
						const words = await wordsOf(page);

						await rendersWell(
							page,
							language,
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
		});
	}
}
