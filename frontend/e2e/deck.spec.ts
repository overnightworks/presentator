import type { APIResponse, Browser, Page } from '@playwright/test';

import type { DeckView } from '../src/lib/api/decks.ts';
import { ageInWords, durationInWords } from '../src/lib/age.ts';
import { type DeckRecord, expect, signIn, test } from './person.ts';
import {
	endsTheSession,
	entriesOf,
	leftForSignIn,
	minutesAgo,
	NOW,
	sessionOf,
	SIGN_IN_START,
	startsSignIn,
	tabbedControlWithRing,
	wordsOf
} from './scenario.ts';

const SOURCE_ADDRESS = 'git@heimserver:decks.git';
const secondsAgo = (seconds: number): string =>
	new Date(Date.parse(NOW) - seconds * 1000).toISOString();
const aDeck = (overrides: Partial<DeckRecord> = {}): DeckRecord => ({
	state: 'ready',
	title: 'Agenten-Fabrik',
	changed_at: minutesAgo(180),
	built_at: minutesAgo(12),
	...overrides
});
const toolchainFailure = {
	reason: 'toolchain',
	text: "slides.md:41:3 Unexpected token '}' in frontmatter"
} as const;
const JSON_HEADERS = { Accept: 'application/json' };

// The ages and the duration as the page words them, through the lobby's own age helpers.
async function agesOf(page: Page) {
	const { language } = await sessionOf(page);
	const now = Date.parse(NOW);
	return {
		built: ageInWords(minutesAgo(12), now, language),
		changed: ageInWords(minutesAgo(180), now, language),
		attempt: ageInWords(minutesAgo(26), now, language),
		building: durationInWords(secondsAgo(40), now, language)
	};
}

async function wireOf(page: Page, slug: string): Promise<DeckView> {
	const answer = await page.request.get(`/api/decks/${slug}`, { headers: JSON_HEADERS });
	expect(answer.ok()).toBe(true);
	return (await answer.json()) as DeckView;
}

// Another person's deck, made in a browser context of its own.
async function aForeignSlug(browser: Browser, baseURL: string | undefined): Promise<string> {
	const foreign = await browser.newContext({ baseURL });
	try {
		const { slugs } = await signIn(foreign, { role: 'admin', decks: [aDeck()] });
		return slugs[0] ?? '';
	} finally {
		await foreign.close();
	}
}

const fiveBoards: DeckRecord[] = [
	aDeck({ title: 'Agenten-Fabrik' }),
	aDeck({
		state: 'building',
		title: 'Kundenfeedback Q3',
		attempt: { started_at: secondsAgo(40) }
	}),
	aDeck({
		state: 'failed',
		title: 'Knowledge Fabric',
		attempt: { started_at: minutesAgo(26), failure: toolchainFailure }
	}),
	aDeck({
		state: 'failed',
		title: 'Frisch gescheitert',
		built_at: undefined,
		attempt: { started_at: minutesAgo(26), failure: { reason: 'no-words' } }
	}),
	aDeck({ state: 'never-built', title: 'Jahresrückblick 2025', built_at: undefined })
];

// Cell 1.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`the five deck boards at ${width} px, ${colorScheme}`, () => {
			test.use({
				person: {
					role: 'admin',
					source: SOURCE_ADDRESS,
					decks: fiveBoards
				},
				locale: 'en-US',
				colorScheme
			});

			test('shows each state with its facts, locks and links, and saves a screenshot', async ({
				page,
				signedIn
			}, testInfo) => {
				const words = await wordsOf(page);
				const ages = await agesOf(page);
				const slugs = signedIn?.slugs ?? [];
				await page.clock.setFixedTime(NOW);
				await page.setViewportSize({ width, height: 900 });
				const [ready, building, failedWithTalk, failedWithout, neverBuilt] = slugs;
				const views = (wire: DeckView) => [
					page.getByRole('link', { name: words['deck.presenterView'] }).and(
						page.locator(`a[href="${wire.talk}presenter/"]`)
					),
					page.getByRole('link', { name: words['deck.projectorView'] }).and(
						page.locator(`a[href="${wire.talk}"]`)
					)
				];

				// Ready.
				let wire = await wireOf(page, ready ?? '');
				await page.goto(`/deck/${ready}`);
				await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
				await expect(page.locator('h1')).toHaveCSS('text-transform', 'none');
				await expect(page.locator('.status .state')).toHaveText(words['deck.stateReady']);
				await expect(page.locator('.status .state')).toHaveAttribute('data-state', 'ready');
				await expect(page.getByText(ready ?? '', { exact: true })).toBeVisible();
				await expect(page.locator('.meta')).toContainText(SOURCE_ADDRESS);
				await expect(page.locator('.meta')).toContainText(wire.commit);
				await expect(page.locator('.meta')).toContainText(
					words['deck.built']({ age: ages.built })
				);
				await expect(page.locator('.status .muted')).toContainText(
					words['deck.buildsWith']({ themes: '' }).trim()
				);
				for (const theme of wire.themes ?? []) {
					await expect(page.locator('.status .muted')).toContainText(theme);
				}
				for (const view of views(wire)) {
					await expect(view).toBeVisible();
				}
				await expect(page.getByRole('link', { name: words['deck.pdf'] })).toHaveAttribute(
					'href',
					`/deck/${ready}/pdf`
				);
				await expect(page.locator('aside.banner')).toHaveCount(0);
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({ path: testInfo.outputPath(`deck-ready-${width}-${colorScheme}.png`), fullPage: true });

				// Building: the earlier talk stays, its views are locked, its PDF is offered.
				wire = await wireOf(page, building ?? '');
				await page.goto(`/deck/${building}`);
				await expect(page.locator('.status .state')).toHaveAttribute('data-state', 'building');
				const banner = page.locator('aside.banner');
				await expect(banner).toHaveAttribute('data-banner', 'building');
				await expect(banner).toContainText(words['deck.buildingLabel']);
				await expect(banner).toContainText(wire.attempt?.commit ?? '');
				await expect(banner).toContainText(words['deck.buildingFor']({ age: ages.building }));
				await expect(banner).toContainText(words['deck.buildingExplanation']);
				await expect(page.locator('.actions .off')).toHaveText([
					words['deck.presenterView'],
					words['deck.projectorView']
				]);
				await expect(page.locator(`a[href^="${wire.talk}"]`)).toHaveCount(0);
				await expect(page.getByRole('link', { name: words['deck.pdf'] })).toBeVisible();
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({ path: testInfo.outputPath(`deck-building-${width}-${colorScheme}.png`), fullPage: true });

				// Failed with a talk that stands.
				wire = await wireOf(page, failedWithTalk ?? '');
				await page.goto(`/deck/${failedWithTalk}`);
				await expect(page.locator('.status .state')).toHaveAttribute('data-state', 'failed');
				await expect(banner).toHaveAttribute('data-banner', 'failed');
				await expect(banner).toContainText(words['deck.failedTitle']);
				await expect(banner).toContainText(words['deck.attemptLabel']);
				await expect(banner).toContainText(wire.attempt?.commit ?? '');
				await expect(banner).toContainText(ages.attempt);
				await expect(banner.locator('pre')).toHaveText(toolchainFailure.text);
				await expect(banner).not.toContainText(words['deck.failedLastTalkStands']);
				await expect(page.getByText(words['deck.failedLastTalkStands'])).toBeVisible();
				for (const view of views(wire)) {
					await expect(view).toBeVisible();
				}
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({ path: testInfo.outputPath(`deck-failed-${width}-${colorScheme}.png`), fullPage: true });

				// Failed with no talk, and nothing said.
				await page.goto(`/deck/${failedWithout}`);
				await expect(banner).toContainText(words['deck.failedWithoutMessage']);
				await expect(banner.locator('pre')).toHaveCount(0);
				await expect(page.getByText(words['deck.failedLastTalkStands'])).toHaveCount(0);
				await expect(page.getByText(words['deck.notBuiltExplanation'])).toBeVisible();
				await expect(page.locator('.actions')).toHaveCount(0);
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({ path: testInfo.outputPath(`deck-failed-no-talk-${width}-${colorScheme}.png`), fullPage: true });

				// Never built.
				await page.goto(`/deck/${neverBuilt}`);
				await expect(page.locator('.status .state')).toHaveText(words['deck.stateNeverBuilt']);
				await expect(page.getByText(words['deck.notBuiltExplanation'])).toBeVisible();
				await expect(page.locator('aside.banner')).toHaveCount(0);
				await expect(page.locator('.actions')).toHaveCount(0);
				for (const age of [ages.built, ages.changed, ages.attempt]) {
					await expect(page.locator('.meta')).not.toContainText(age);
				}
				const overflow = await page.evaluate(
					() => document.documentElement.scrollWidth - document.documentElement.clientWidth
				);
				expect(overflow).toBeLessThanOrEqual(0);
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({ path: testInfo.outputPath(`deck-never-built-${width}-${colorScheme}.png`), fullPage: true });
			});
		});
	}
}

// Cell 2 and 3.
const reasons: {
	reason: NonNullable<DeckRecord['attempt']>['failure'] & object;
	counts: string[];
}[] = [
	{ reason: toolchainFailure, counts: [] },
	{ reason: { reason: 'output-megabytes', written_megabytes: 700, allowed_megabytes: 500 }, counts: ['700', '500'] },
	{ reason: { reason: 'output-entries', allowed_entries: 20000 }, counts: ['20000'] },
	{ reason: { reason: 'output-shape' }, counts: [] },
	{ reason: { reason: 'deck-megabytes', allowed_megabytes: 400, allowed_entries: 9000 }, counts: ['400', '9000'] },
	{ reason: { reason: 'deck-paths' }, counts: [] }
];
const markup = "<script>window.name='ran'</script><b>bold</b>";

test.describe('what a failed build says', () => {
	test.use({
		person: {
			role: 'admin',
			decks: [
				...reasons.map(({ reason }, index) =>
					aDeck({
						state: 'failed',
						title: `Failed ${index}`,
						built_at: undefined,
						attempt: { failure: reason }
					})
				),
				aDeck({
					state: 'failed',
					title: 'Markup',
					built_at: undefined,
					attempt: { failure: { reason: 'toolchain', text: markup } }
				})
			]
		}
	});

	test('words each failure reason with its counts and no placeholder left over', async ({
		page,
		signedIn
	}) => {
		for (const [index, { reason, counts }] of reasons.entries()) {
			await page.goto(`/deck/${signedIn?.slugs[index]}`);

			const said = page.locator('aside.banner pre');
			await expect(said).toBeVisible();
			expect((await said.textContent())?.trim().length).toBeGreaterThan(0);
			for (const count of counts) {
				await expect(said).toContainText(count);
			}
			if (reason.reason !== 'toolchain') {
				await expect(said).not.toContainText(/[{}]/);
			}
		}
	});

	test('shows markup in a toolchain failure as text', async ({ page, signedIn }) => {
		await page.goto(`/deck/${signedIn?.slugs[reasons.length]}`);

		await expect(page.locator('aside.banner pre')).toHaveText(markup);
		await expect(page.locator('aside.banner pre b')).toHaveCount(0);
		expect(await page.evaluate(() => window.name)).not.toBe('ran');
	});
});

// Cell 4.
test.describe("an unknown deck and another person's", () => {
	test.use({ person: { role: 'admin' } });

	test('show the same board inside the lobby at 200', async ({ page, browser, baseURL }) => {
		const words = await wordsOf(page);
		const foreign = await aForeignSlug(browser, baseURL);
		const boards: { text: string | null; title: string }[] = [];

		for (const slug of [foreign, 'no-deck-by-this-name']) {
			const answer = await page.goto(`/deck/${slug}`);

			expect(answer?.status()).toBe(200);
			await expect(page.locator('main h1')).toHaveText(words['deck.unknownTitle']);
			await expect(page.locator('main p')).toHaveText(words['deck.unknownExplanation']);
			await expect(page.locator('header.top .wordmark')).toHaveText(words['app.name']);
			await expect(page.locator('main a.btn')).toHaveText(words['deck.decksLink']);
			boards.push({ text: await page.locator('main').textContent(), title: await page.title() });
		}

		expect(boards[0]).toEqual(boards[1]);
		expect(boards[0]?.title).toBe(words['deck.unknownTitle']);
	});
});

// Cells 5 to 7.
test.describe('the deck page inside the app', () => {
	test.use({
		person: { role: 'admin', decks: [aDeck()] },
		locale: 'en-US'
	});

	// Cell 5.
	test('is reached by a row click, left and entered again by back and forward, in one document', async ({
		page,
		signedIn
	}) => {
		const words = await wordsOf(page);
		await page.clock.setFixedTime(NOW);
		await page.goto('/');
		const entries = await entriesOf(page);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await page.getByRole('link', { name: 'Agenten-Fabrik' }).click();
		await expect(page).toHaveURL(new RegExp(`/deck/${signedIn?.slugs[0]}$`));
		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
		expect(await entriesOf(page)).toBe(entries + 1);
		await page.goBack();
		await expect(page.getByRole('heading', { name: words['deck.decksLink'] })).toBeVisible();
		await page.goForward();
		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');

		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		expect(await entriesOf(page)).toBe(entries + 1);
	});

	// Cell 6.
	test('marks Decks as the current section on / and on the deck page, after back too', async ({
		page
	}) => {
		const words = await wordsOf(page);
		const current = page.locator('header.top nav [aria-current]');
		await page.goto('/');
		await expect(current).toHaveText(words['header.decks']);

		await page.getByRole('link', { name: 'Agenten-Fabrik' }).click();
		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
		await expect(current).toHaveText(words['header.decks']);
		await expect(current).toHaveAttribute('aria-current', 'page');

		await page.goBack();
		await expect(page.getByRole('heading', { name: words['deck.decksLink'] })).toBeVisible();
		await expect(current).toHaveText(words['header.decks']);
	});

	// Cell 7.
	test('an ended session meets the sign-in once, on a row click and on the back link', async ({
		page,
		context,
		signedIn
	}) => {
		const words = await wordsOf(page);
		const signInRequests: string[] = [];
		page.on('request', (sent) => {
			if (new URL(sent.url()).pathname === SIGN_IN_START) {
				signInRequests.push(new URL(sent.url()).search);
			}
		});
		await page.goto('/');
		await expect(page.getByRole('heading', { name: words['deck.decksLink'] })).toBeVisible();
		await endsTheSession(context);
		const started = startsSignIn(page, `%2Fdeck%2F${signedIn?.slugs[0]}`);

		await page.getByRole('link', { name: 'Agenten-Fabrik' }).click();

		await leftForSignIn(page, started);
		expect(signInRequests).toEqual([`?next=%2Fdeck%2F${signedIn?.slugs[0]}`]);
	});

	test('an ended session meets the sign-in once from the deck page, on the back link', async ({
		page,
		context,
		signedIn
	}) => {
		const words = await wordsOf(page);
		const signInRequests: string[] = [];
		page.on('request', (sent) => {
			if (new URL(sent.url()).pathname === SIGN_IN_START) {
				signInRequests.push(new URL(sent.url()).search);
			}
		});
		await page.goto(`/deck/${signedIn?.slugs[0]}`);
		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
		await endsTheSession(context);
		const started = startsSignIn(page);

		await page.getByRole('link', { name: words['deck.back'] }).click();

		await leftForSignIn(page, started);
		expect(signInRequests).toEqual(['?next=%2F']);
	});
});

// Cell 8.
test.describe('signed out', () => {
	test('a deck address requests the sign-in with next set to that address', async ({
		page,
		browser,
		baseURL
	}) => {
		const slug = await aForeignSlug(browser, baseURL);
		const started = startsSignIn(page, `%2Fdeck%2F${slug}`);

		await page.goto(`/deck/${slug}`).catch(() => undefined);

		await leftForSignIn(page, started);
	});
});

// Cell 9.
test.describe('the PDF', () => {
	test.use({
		person: {
			role: 'admin',
			decks: [aDeck(), aDeck({ title: 'Ohne PDF', pdf: 'missing' })]
		}
	});

	test("downloads under the deck's slug as a document navigation", async ({ page, signedIn }) => {
		const words = await wordsOf(page);
		await page.goto(`/deck/${signedIn?.slugs[0]}`);

		const [download] = await Promise.all([
			page.waitForEvent('download'),
			page.getByRole('link', { name: words['deck.pdf'] }).click()
		]);

		expect(download.suggestedFilename()).toBe(`${signedIn?.slugs[0]}.pdf`);
	});

	test('a missing PDF answers 404 with the not-found board and the header', async ({
		page,
		signedIn
	}) => {
		const words = await wordsOf(page);

		const answer = await page.goto(`/deck/${signedIn?.slugs[1]}/pdf`);

		expect(answer?.status()).toBe(404);
		await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
		await expect(page.locator('header.top .wordmark')).toHaveText(words['app.name']);
		await expect(page.locator('header.top nav [aria-current]')).toHaveCount(0);
	});

	test('a foreign slug and an unknown slug answer alike', async ({ page, browser, baseURL }) => {
		const foreign = await aForeignSlug(browser, baseURL);
		const comparable = async (slug: string) => {
			const answer: APIResponse = await page.request.get(`/deck/${slug}/pdf`, {
				headers: { Accept: 'text/html' }
			});
			const headers = answer
				.headersArray()
				.filter(({ name }) => !['date', 'set-cookie'].includes(name.toLowerCase()))
				.map(({ name, value }) => [name.toLowerCase(), value])
				.sort();
			const cookieNames = answer
				.headersArray()
				.filter(({ name }) => name.toLowerCase() === 'set-cookie')
				.map(({ value }) => value.split('=')[0])
				.sort();
			return { status: answer.status(), headers, cookieNames, body: await answer.text() };
		};

		const foreignAnswer = await comparable(foreign);
		const unknownAnswer = await comparable('no-deck-by-this-name');

		expect(foreignAnswer.status).toBe(404);
		expect(foreignAnswer).toEqual(unknownAnswer);
	});
});

// Cell 10.
test.describe('the slash address of a deck', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	test('loads the deck page and the router drops the slash from the address', async ({ page, signedIn }) => {
		await page.goto(`/deck/${signedIn?.slugs[0]}/`);

		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
		await expect(page).toHaveURL(new RegExp(`/deck/${signedIn?.slugs[0]}$`));
	});
});

// Cell 11.
test.describe('sentence 9 on the deck page', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	test('shows a focus ring on the back link, both views and the PDF at 390 px', async ({
		page,
		signedIn
	}) => {
		const words = await wordsOf(page);
		await page.setViewportSize({ width: 390, height: 844 });
		await page.goto(`/deck/${signedIn?.slugs[0]}`);
		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');

		const focused: string[] = [];
		for (let tab = 0; tab < 12; tab++) {
			const control = await tabbedControlWithRing(page);
			if (control) {
				focused.push(control);
			}
		}

		for (const name of [
			words['deck.back'],
			words['deck.presenterView'],
			words['deck.projectorView'],
			words['deck.pdf']
		]) {
			expect(focused).toContain(name);
		}
	});
});

// Cell 12.
test.describe('rows that are absent', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	test('shows no Builds-with row while the toolchain cannot be read, and no source row without a source', async ({
		page,
		signedIn
	}) => {
		const slug = signedIn?.slugs[0] ?? '';
		await page.route(`**/api/decks/${slug}`, async (route) => {
			const answer = await route.fetch();
			const deck = (await answer.json()) as DeckView;
			await route.fulfill({ response: answer, json: { ...deck, themes: null } });
		});

		await page.goto(`/deck/${slug}`);

		await expect(page.locator('h1')).toHaveText('Agenten-Fabrik');
		await expect(page.locator('.status .muted')).toHaveCount(0);
		await expect(page.locator('.meta code')).toHaveCount(1);
	});
});
