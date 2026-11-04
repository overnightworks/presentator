import type { Page, Route } from '@playwright/test';

import type { SourceSummary } from '../src/lib/api/sources.ts';
import { expect, test } from './person.ts';
import {
	endsTheSession,
	entriesOf,
	expectsHeaderOnOneLine,
	expectsNoHorizontalScroll,
	leftForSignIn,
	SIGN_IN_START,
	sessionOf,
	startsSignIn,
	tabbedControlWithRing
} from './scenario.ts';
import {
	aName,
	anHttpsAddress,
	createsASource,
	NEW_ADDRESS,
	opensTheForm,
	SOURCES_API,
	SOURCES_LIST,
	sourceAddress,
	sourceNames,
	tabsOf
} from './sources.ts';

const NEXT = '%2Fsettings%2Fsources';
const MINUTE = 60_000;

// The list words ages from the browser's clock, so a scenario's instants are relative to it.
const minutesAgo = (minutes: number): string => new Date(Date.now() - minutes * MINUTE).toISOString();

const aSummary = (name: string, overrides: Partial<SourceSummary> = {}): SourceSummary => ({
	name,
	url: `https://git.example.invalid/${name}.git`,
	access: 'https-token',
	state: 'reachable',
	reason: null,
	fetched_at: minutesAgo(3),
	...overrides
});

// The list's answer, replaced; the list is instance-wide and the stack is shared,
// so a cell that counts rows or needs a state names its own answer.
async function listAnswers(page: Page, sources: SourceSummary[]): Promise<void> {
	await page.route(`**${SOURCES_API}`, (route: Route) =>
		new URL(route.request().url()).pathname === SOURCES_API && route.request().method() === 'GET'
			? route.fulfill({ json: { sources } })
			: route.continue()
	);
}

const rowOf = (page: Page, name: string) =>
	page.locator('table.sources tbody tr').filter({ has: page.getByRole('link', { name, exact: true }) });

const stillTheSameDocument = (page: Page) =>
	page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
const isStillTheSameDocument = (page: Page): Promise<boolean> =>
	page.evaluate(() => 'stillTheSameDocument' in window);

// The documents a page requests from now on: none when every move is a client navigation.
function documentsRequested(page: Page): string[] {
	const documents: string[] = [];
	page.on('request', (sent) => {
		if (sent.resourceType() === 'document') {
			documents.push(sent.url());
		}
	});
	return documents;
}

const boardRows = [
	aSummary('firmen-gitlab'),
	aSummary('github-privat', {
		url: 'git@github.com:felix/decks.git',
		access: 'ssh-deploy-key',
		state: 'refused',
		reason: 'refused',
		fetched_at: minutesAgo(120)
	}),
	aSummary('heimserver', {
		url: 'git@heimserver:decks.git',
		access: 'ssh-deploy-key',
		state: 'never-fetched',
		fetched_at: null
	})
];

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 1.
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`the board's three rows at ${width} px, ${colorScheme}`, () => {
				test.use({ colorScheme });

				test('shows reachable, refused with its reason and never fetched, and saves a screenshot', async ({
					page
				}, testInfo) => {
					const { words } = await sessionOf(page);
					await page.setViewportSize({ width, height: 900 });
					await listAnswers(page, boardRows);

					await page.goto(SOURCES_LIST);

					await expect(page.locator('table.sources tbody tr')).toHaveCount(3);
					await expect(rowOf(page, 'github-privat')).toContainText(words.source_run_refused);
					await expectsNoHorizontalScroll(page);
					if (width === 390) {
						await expectsHeaderOnOneLine(page);
					}
					const fetchNow = page.getByRole('button', { name: words.sources_fetch_now }).first();
					const box = await fetchNow.boundingBox();
					expect(box?.width).toBeGreaterThan(80);
					await fetchNow.focus();
					await page.keyboard.press('Shift+Tab');
					await page.keyboard.press('Tab');
					await page.evaluate(() => document.fonts.ready);
					await page.screenshot({
						path: testInfo.outputPath(`sources-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}

	// Cell 2.
	test('words every state, reason and age of the answer, the null access and the never fetched', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await listAnswers(page, [
			aSummary('Reach-Able'),
			aSummary('refused-one', { state: 'refused', reason: 'refused', access: 'ssh-deploy-key' }),
			aSummary('failed-one', { state: 'failed', reason: 'failed', access: 'local-folder' }),
			aSummary('dead-host', { state: 'error', reason: 'unreachable' }),
			aSummary('lost-secret', { state: 'error', reason: 'credential-unresolvable' }),
			aSummary('no-reason', { state: 'failed', reason: null }),
			aSummary('plain-http', { access: null }),
			aSummary('never-one', { state: 'never-fetched', fetched_at: null })
		]);

		await page.goto(SOURCES_LIST);

		const states: [string, string][] = [
			['Reach-Able', words.source_state_reachable],
			['refused-one', words.source_state_refused],
			['failed-one', words.source_state_failed],
			['dead-host', words.source_state_error],
			['lost-secret', words.source_state_error],
			['no-reason', words.source_state_failed],
			['never-one', words.source_state_never_fetched]
		];
		for (const [name, word] of states) {
			await expect(rowOf(page, name).locator('.state')).toHaveText(word);
		}
		const reasons: [string, string][] = [
			['refused-one', words.source_run_refused],
			['failed-one', words.source_run_failed],
			['dead-host', words.source_run_unreachable],
			['lost-secret', words.source_run_secret]
		];
		for (const [name, sentence] of reasons) {
			await expect(rowOf(page, name).locator('.reason')).toHaveText(sentence);
		}
		await expect(page.locator('.reason')).toHaveCount(reasons.length);
		await expect(rowOf(page, 'no-reason').locator('.reason')).toHaveCount(0);
		await expect(rowOf(page, 'plain-http').locator('.access')).toHaveText('');
		await expect(rowOf(page, 'refused-one').locator('.access')).toHaveText(
			words.source_access_deploy_key
		);
		await expect(rowOf(page, 'never-one').locator('.fetched')).toHaveText('—');
		await expect(rowOf(page, 'Reach-Able').locator('a.as-typed')).toHaveText('Reach-Able');
		await expect(page.locator('table.sources')).not.toContainText('credential-unresolvable');
		await expect(page.locator('table.sources')).not.toContainText('never-fetched');
	});

	// Cell 3.
	test('an empty list shows its title, what a source needs and an Add source button', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await listAnswers(page, []);

		await page.goto(SOURCES_LIST);

		const empty = page.locator('section.empty');
		await expect(empty.getByRole('heading', { name: words.sources_empty_title })).toBeVisible();
		await expect(empty).toContainText(words.sources_empty_explanation);
		await expect(empty).toContainText(words.sources_empty_need);
		await expect(empty.locator('code').nth(0)).toHaveText(words.sources_empty_ssh);
		await expect(empty.locator('code').nth(1)).toHaveText(words.sources_empty_https);
		await expect(page.locator('table')).toHaveCount(0);
		await expect(empty.getByRole('link', { name: words.sources_add })).toBeVisible();
	});

	// Cell 4.
	for (const where of ['the head', 'the empty state'] as const) {
		test(`Add source in ${where} opens the form by client navigation and its back link returns`, async ({
			page
		}) => {
			const { words } = await sessionOf(page);
			if (where === 'the empty state') {
				await listAnswers(page, []);
			}
			await page.goto(SOURCES_LIST);
			await stillTheSameDocument(page);
			const documents = documentsRequested(page);
			const add =
				where === 'the head' ? page.locator('.head') : page.locator('section.empty');

			await add.getByRole('link', { name: words.sources_add }).click();
			await expect(page).toHaveURL(NEW_ADDRESS);
			await page.getByRole('link', { name: words.sources_back }).click();

			await expect(page).toHaveURL(SOURCES_LIST);
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_sources);
			expect(await isStillTheSameDocument(page)).toBe(true);
			expect(documents).toEqual([]);
		});
	}

	// Cell 5.
	test('a name opens the source page by client navigation, its back link returns and Back and Forward move between them', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await createsASource(page, words, { name, url: anHttpsAddress(name) });
		await page.goto(SOURCES_LIST);
		await stillTheSameDocument(page);
		const documents = documentsRequested(page);

		await rowOf(page, name).getByRole('link', { name, exact: true }).click();
		await expect(page).toHaveURL(sourceAddress(name));
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
		await expect(tabsOf(page, words).getByRole('link', { name: words.settings_sources })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await page.getByRole('link', { name: words.sources_back }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await page.goBack();
		await expect(page).toHaveURL(sourceAddress(name));
		await page.goForward();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(tabsOf(page, words).getByRole('link', { name: words.settings_sources })).toHaveAttribute(
			'aria-current',
			'page'
		);

		expect(await isStillTheSameDocument(page)).toBe(true);
		expect(documents).toEqual([]);
	});

	// Cell 6.
	test('two real sources are listed in the order of the list answer', async ({ page }, testInfo) => {
		const { words } = await sessionOf(page);
		const first = aName(testInfo, 'first');
		const second = aName(testInfo, 'second');
		for (const name of [first, second]) {
			await opensTheForm(page);
			await createsASource(page, words, { name, url: anHttpsAddress(name) });
		}
		const ordered = (await sourceNames(page)).filter((name) => [first, second].includes(name));

		await page.goto(SOURCES_LIST);
		await expect(page.locator('table.sources')).toBeVisible();

		const shown = await page.locator('table.sources a.as-typed').allTextContents();
		expect(shown.filter((name) => [first, second].includes(name))).toEqual(ordered);
		expect(ordered).toHaveLength(2);
	});

	// Cell 7.
	test('Fetch now posts once to its row, re-reads the list in place, leaves the other row and disables every button meanwhile', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const first = aName(testInfo, 'first');
		const second = aName(testInfo, 'second');
		for (const name of [first, second]) {
			await opensTheForm(page);
			await createsASource(page, words, { name, url: anHttpsAddress(name) });
		}
		await page.goto(SOURCES_LIST);
		await stillTheSameDocument(page);
		const entries = await entriesOf(page);
		const otherBefore = await rowOf(page, second).innerText();
		const posts: string[] = [];
		let release!: () => void;
		const gate = new Promise<void>((resolve) => (release = resolve));
		await page.route('**/api/sources/*/fetch', async (route) => {
			posts.push(new URL(route.request().url()).pathname);
			await gate;
			await route.continue();
		});
		const reread = page.waitForResponse(
			(received) =>
				new URL(received.url()).pathname === SOURCES_API && received.request().method() === 'GET'
		);

		const fetchNow = rowOf(page, first).getByRole('button', { name: words.sources_fetch_now });
		await fetchNow.click();

		for (const button of await page.locator('table.sources button').all()) {
			await expect(button).toBeDisabled();
		}
		release();
		await reread;
		await expect(fetchNow).toBeEnabled();
		expect(posts).toEqual([`/api/sources/${first}/fetch`]);
		expect(await rowOf(page, second).innerText()).toBe(otherBefore);
		expect(await entriesOf(page)).toBe(entries);
		expect(await isStillTheSameDocument(page)).toBe(true);
	});

	// Cell 8.
	test('with the session ended, Fetch now leaves for the sign-in once with the list as next', async ({
		page,
		context
	}) => {
		const { words } = await sessionOf(page);
		await listAnswers(page, [aSummary('some-source')]);
		await page.goto(SOURCES_LIST);
		await expect(page.getByRole('button', { name: words.sources_fetch_now })).toBeVisible();
		const signIns: string[] = [];
		page.on('request', (sent) => {
			if (new URL(sent.url()).pathname === SIGN_IN_START) {
				signIns.push(new URL(sent.url()).search);
			}
		});
		await endsTheSession(context);
		const started = startsSignIn(page, NEXT);

		await page.getByRole('button', { name: words.sources_fetch_now }).click({ noWaitAfter: true });

		await leftForSignIn(page, started);
		expect(signIns).toEqual([`?next=${NEXT}`]);
	});

	// Cell 12.
	test('every control shows a focus ring at 390 px, in order', async ({ page }) => {
		const { words } = await sessionOf(page);
		await page.setViewportSize({ width: 390, height: 900 });
		await listAnswers(page, [aSummary('one-source'), aSummary('another-source')]);
		await page.goto(SOURCES_LIST);
		await page.locator('body').click({ position: { x: 1, y: 1 } });

		const focused: (string | null)[] = [];
		for (let tab = 0; tab < 14; tab++) {
			focused.push(await tabbedControlWithRing(page));
		}

		const controlsInOrder = [
			words.sources_add,
			'one-source',
			words.sources_fetch_now,
			'another-source',
			words.sources_fetch_now
		];
		let from = 0;
		for (const control of controlsInOrder) {
			const at = focused.indexOf(control, from);
			expect(at, `${control} in ${focused.join(' | ')}`).toBeGreaterThanOrEqual(0);
			from = at + 1;
		}
	});

	// Cell 13.
	test('Settings is the current section, and General then Sources by the tab strip is a client navigation both ways', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await page.goto(SOURCES_LIST);
		await stillTheSameDocument(page);
		const documents = documentsRequested(page);
		const header = page.locator('header.top');
		await expect(header.getByRole('link', { name: words.section_settings })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await expect(header.getByRole('link', { name: words.section_decks, exact: true })).not.toHaveAttribute(
			'aria-current',
			'page'
		);

		await tabsOf(page, words).getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL('/settings');
		await tabsOf(page, words).getByRole('link', { name: words.settings_sources }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(tabsOf(page, words).getByRole('link', { name: words.settings_sources })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await page.goBack();
		await expect(page).toHaveURL('/settings');
		await page.goForward();
		await expect(page).toHaveURL(SOURCES_LIST);

		expect(await isStillTheSameDocument(page)).toBe(true);
		expect(documents).toEqual([]);
	});
});

// Cell 11.
test.describe('in the instance language', () => {
	test.use({ person: { role: 'admin' } });

	test('words the age of the fetch in the browser, in the instance language', async ({ page }) => {
		const session = await sessionOf(page);
		await listAnswers(page, [aSummary('one-source', { fetched_at: minutesAgo(3 * 24 * 60) })]);

		await page.goto(SOURCES_LIST);

		const expected = await page.evaluate(
			(tag) => new Intl.RelativeTimeFormat(tag, { numeric: 'auto' }).format(-3, 'day'),
			session.words.language_tag
		);
		await expect(page.locator('.fetched')).toHaveText(expected);
		await expect(page.locator('main h1')).toHaveText(session.words.settings_sources);
	});
});

// Cell 10.
test.describe('a member', () => {
	test.use({ person: { role: 'user' } });

	test('meets the for-admins board with no controls and no tab strip', async ({ page }) => {
		const { words } = await sessionOf(page);

		await page.goto(SOURCES_LIST);

		await expect(page.locator('main h1')).toHaveText(words.for_admins_title);
		await expect(page.getByRole('navigation', { name: words.section_settings })).toHaveCount(0);
		await expect(page.locator('main button')).toHaveCount(0);
	});
});

// Cell 9.
test('a signed-out list address is sent to the sign-in with next set to it', async ({ page }) => {
	const started = startsSignIn(page, NEXT);

	await page.goto(SOURCES_LIST).catch(() => undefined);

	await leftForSignIn(page, started);
});
