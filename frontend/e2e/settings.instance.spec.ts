import type { Page } from '@playwright/test';

import type { SettingsView, SettingsWrite } from '../src/lib/api/settings.ts';
import { type Language, languages, sourceLanguage } from '../src/lib/words/index.ts';
import { type DeckRecord, expect, signIn, test as base } from './person.ts';
import {
	chooseTheme,
	endsTheSession,
	entriesOf,
	expectsHeaderOnOneLine,
	expectsNoHorizontalScroll,
	leftForSignIn,
	MENU,
	minutesAgo,
	NOW,
	opensTheMenu,
	sessionOf,
	startsSignIn,
	tabbedControlWithRing,
	themeRow,
	wordsOf
} from './scenario.ts';
import { answersWith, OWN_SOURCE, ownSourceName, sourceAddress } from './sources.ts';

// The instance defaults are one store per stack, so every test that asserts a
// stored value or a repaint first arranges its own baseline through the API.
const JSON_ACCEPT = { Accept: 'application/json' };
const SAME_ORIGIN = { ...JSON_ACCEPT, 'Sec-Fetch-Site': 'same-origin' };
const SETTINGS_ADDRESS = '/settings';
const SETTINGS_API = '/api/settings';

async function storedSettings(page: Page): Promise<SettingsView> {
	const answer = await page.request.get(SETTINGS_API, { headers: JSON_ACCEPT });
	expect(answer.status()).toBe(200);
	return (await answer.json()) as SettingsView;
}

// The name a language gives itself, as the select offers it.
const languageName = (tag: Language): string =>
	new Intl.DisplayNames(tag, { type: 'language' }).of(tag) ?? tag;

// The baseline speaks a language other than the one the words are written in, so
// no test restates a tag, none depends on what an earlier test left behind, and
// a test that words a page in the written language always has a change to make.
const baselineLanguage = languages.find((language) => language !== sourceLanguage) as Language;
const test = base.extend<{
	arrangeBaseline: (name?: string) => Promise<SettingsView>;
}>({
	arrangeBaseline: async ({ page }, use) => {
		await use(async (name = 'Heim') => {
			const baseline: SettingsWrite = { name, language_tag: baselineLanguage };
			const answer = await page.request.put(SETTINGS_API, {
				headers: SAME_ORIGIN,
				data: baseline
			});
			expect(answer.status()).toBe(200);
			return storedSettings(page);
		});
	}
});

// A language the baseline does not speak.
function otherLanguage(settings: SettingsView): { tag: Language; name: string } {
	const tag = languages.find((language) => language !== settings.language_tag);
	expect(tag).toBeDefined();
	return { tag: tag as Language, name: languageName(tag as Language) };
}

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`Settings General at ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'admin' }, colorScheme });

			test('shows the tab strip, the three fields, the hint and Save, each with a ring', async ({
				page,
				arrangeBaseline
			}, testInfo) => {
				const settings = await arrangeBaseline();
				const words = await wordsOf(page);
				await page.setViewportSize({ width, height: 900 });

				await page.goto(SETTINGS_ADDRESS);

				const tabs = page.getByRole('navigation', { name: words['settings.navLabel'] });
				await expect(tabs.getByRole('link', { name: words['settings.generalTab'] })).toHaveAttribute(
					'aria-current',
					'page'
				);
				await expect(tabs.getByRole('link', { name: words['settings.sourcesTab'] })).not.toHaveAttribute(
					'aria-current'
				);
				await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
				await expect(page.getByLabel(words['settings.instanceName'])).toHaveValue(settings.name);
				const language = page.getByLabel(words['settings.defaultLanguage']);
				await expect(language.locator('option')).toHaveText(
					settings.languages.map(languageName)
				);
				await expect(language.locator('option:checked')).toHaveText(
					languageName(settings.language_tag as Language)
				);
				await expect(page.getByRole('button', { name: words['settings.save'] })).toBeEnabled();
				const hint = page.getByRole('button', { name: words['settings.languageHint'] });
				await hint.click();
				await expect(page.getByText(words['settings.languageHint'], { exact: true })).toBeVisible();
				await page.keyboard.press('Escape');

				if (width === 390) {
					await expectsNoHorizontalScroll(page);
					await expectsHeaderOnOneLine(page);
				}
				await page.locator('body').click({ position: { x: 1, y: 1 } });
				const focused: (string | null)[] = [];
				for (let tab = 0; tab < 12; tab++) {
					focused.push(await tabbedControlWithRing(page));
				}
				// A select is named by its options, the info button by its class.
				expect(focused).toEqual(
					expect.arrayContaining([
						words['settings.instanceName'],
						settings.languages.map(languageName).join(''),
						'info',
						words['settings.save']
					])
				);
				await page.evaluate(() => document.fonts.ready);
				await page.screenshot({
					path: testInfo.outputPath(`settings-${width}-${colorScheme}.png`),
					fullPage: true
				});
			});
		});
	}
}

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin' } });

	test('Settings is a client navigation, current beside Decks, and back and forward land right', async ({
		page
	}) => {
		const words = await wordsOf(page);
		const documents: string[] = [];
		page.on('request', (sent) => {
			if (sent.isNavigationRequest()) {
				documents.push(new URL(sent.url()).pathname);
			}
		});
		await page.goto('/');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['deck.decksLink']);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
		documents.length = 0;

		await page.getByRole('link', { name: words['settings.navLabel'] }).click();

		await expect(page).toHaveURL(/\/settings$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
		await expect(page.getByRole('link', { name: words['settings.navLabel'] })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await expect(page.getByRole('link', { name: words['header.decks'] })).not.toHaveAttribute(
			'aria-current'
		);
		await page.goBack();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['deck.decksLink']);
		await page.goForward();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
		expect(documents).toEqual([]);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
	});

	test('Sources is a client navigation to the list, and its General link comes back', async ({
		page
	}) => {
		const words = await wordsOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await page.getByRole('link', { name: words['settings.sourcesTab'] }).click();

		await expect(page).toHaveURL(/\/settings\/sources$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['sources.title']);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		await page.getByRole('link', { name: words['settings.generalTab'] }).click();
		await expect(page).toHaveURL(/\/settings$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
	});

	// LB3.
	test('Save repaints the same document with the stored answer and a reload keeps it', async ({
		page,
		browser,
		arrangeBaseline
	}, testInfo) => {
		const settings = await arrangeBaseline();
		const before = await wordsOf(page);
		const second = otherLanguage(settings);
		await page.goto(SETTINGS_ADDRESS);
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
		const entries = await entriesOf(page);
		// A document navigation would drop this mark.
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await page.getByLabel(before['settings.instanceName']).fill('Hafen');
		await page.getByLabel(before['settings.defaultLanguage']).selectOption(second.tag);
		await page.getByRole('button', { name: before['settings.save'] }).click();

		await expect(page.locator('html')).toHaveAttribute('lang', second.tag);
		const after = await wordsOf(page);
		expect((await sessionOf(page)).language).toBe(second.tag);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(after['settings.title']);
		await expect(page.getByLabel(after['settings.instanceName'])).toHaveValue('Hafen');
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		expect(await entriesOf(page)).toBe(entries);
		await page.reload();
		await expect(page.getByLabel(after['settings.instanceName'])).toHaveValue('Hafen');

		const neighbour = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
		try {
			await signIn(neighbour, { role: 'user' });
			const seen = await neighbour.newPage();
			await seen.goto('/');
			await expect(seen.locator('html')).toHaveAttribute('lang', second.tag);
		} finally {
			await neighbour.close();
		}
	});

	// LB1.
	test('The person menu and the sign-in pages speak the instance language', async ({
		page,
		browser,
		arrangeBaseline
	}, testInfo) => {
		const settings = await arrangeBaseline();
		const second = otherLanguage(settings);
		const saved = await page.request.put(SETTINGS_API, {
			headers: SAME_ORIGIN,
			data: { name: settings.name, language_tag: second.tag } satisfies SettingsWrite
		});
		expect(saved.status()).toBe(200);
		const words = await wordsOf(page);
		expect((await sessionOf(page)).language).toBe(second.tag);

		await page.goto('/');
		await opensTheMenu(page);
		for (const row of ['followSystem', 'light', 'dark'] as const) {
			await expect(await themeRow(page, row)).toBeVisible();
		}
		await expect(page.locator(MENU).getByText(words['account.theme'], { exact: true })).toBeVisible();
		await expect(page.locator(MENU).getByRole('button', { name: words['logout.button'] })).toBeVisible();

		const visitor = await browser.newContext({ baseURL: testInfo.project.use.baseURL });
		try {
			const seen = await visitor.newPage();
			const noAccess = words['signIn.noAccessSentence']({ app: words['app.name'] });
			const boards = [
				{ path: '/login', shown: [words['signIn.loggedOut'], words['signIn.logIn']] },
				...['/login/login-failed', '/login/talk/login-failed'].map((path) => ({
					path,
					shown: [words['signIn.loginFailed'], words['signIn.loginFailedBanner'], words['signIn.tryAgain']]
				})),
				...['/login/no-access', '/login/talk/no-access'].map((path) => ({
					path,
					shown: [words['signIn.noAccess'], noAccess, words['logout.button']]
				}))
			];
			for (const board of boards) {
				await seen.goto(board.path);
				for (const word of board.shown) {
					await expect(seen.locator('.signin')).toContainText(word);
				}
			}
		} finally {
			await visitor.close();
		}
	});

	test('Saving the name leaves an admin whose own theme is Light on Light', async ({
		page,
		arrangeBaseline
	}) => {
		await arrangeBaseline();
		const words = await wordsOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await chooseTheme(page, 'light');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
		await page.keyboard.press('Escape');

		await page.getByLabel(words['settings.instanceName']).fill('Hafen');
		const sessionRead = page.waitForResponse(
			(received) =>
				new URL(received.url()).pathname === '/api/session' && received.request().method() === 'GET'
		);
		await page.getByRole('button', { name: words['settings.save'] }).click();

		await expect.poll(async () => (await storedSettings(page)).name).toBe('Hafen');
		await sessionRead;
		await expect(page.getByRole('button', { name: words['settings.save'] })).toBeEnabled();
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
	});

	test('Enter in the name field saves', async ({ page, arrangeBaseline }) => {
		await arrangeBaseline();
		const words = await wordsOf(page);
		await page.goto(SETTINGS_ADDRESS);

		await page.getByLabel(words['settings.instanceName']).fill('Hafen');
		await page.getByLabel(words['settings.instanceName']).press('Enter');

		await expect.poll(async () => (await storedSettings(page)).name).toBe('Hafen');
	});

	test('a failed write repaints nothing, enables Save again and keeps what was typed', async ({
		page,
		arrangeBaseline
	}) => {
		const baseline = await arrangeBaseline();
		const words = await wordsOf(page);
		const errors: Error[] = [];
		page.on('pageerror', (error) => errors.push(error));
		await page.route(`**${SETTINGS_API}`, (route) =>
			route.request().method() === 'PUT' ? route.fulfill({ status: 500 }) : route.continue()
		);
		await page.goto(SETTINGS_ADDRESS);
		await page.getByLabel(words['settings.instanceName']).fill('Hafen');

		await page.getByRole('button', { name: words['settings.save'] }).click();

		await expect.poll(() => errors.length).toBe(1);
		await expect(page.getByRole('button', { name: words['settings.save'] })).toBeEnabled();
		await expect(page.getByLabel(words['settings.instanceName'])).toHaveValue('Hafen');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
		await page.unroute(`**${SETTINGS_API}`);
		expect(await storedSettings(page)).toEqual(baseline);
	});

	test('an ended session meets the sign-in when Save is pressed', async ({ page, context }) => {
		const words = await wordsOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
		await endsTheSession(context);
		const started = startsSignIn(page, '%2Fsettings');

		await page.getByRole('button', { name: words['settings.save'] }).click();

		await leftForSignIn(page, started);
	});

	test('an ended session meets the sign-in on the Settings link, naming /settings', async ({
		page,
		context
	}) => {
		const words = await wordsOf(page);
		await page.goto('/');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['deck.decksLink']);
		await endsTheSession(context);
		const started = startsSignIn(page, '%2Fsettings');

		await page.getByRole('link', { name: words['settings.navLabel'] }).click();

		await leftForSignIn(page, started);
	});

	test('an unknown address keeps its words and title', async ({ page }) => {
		const words = await wordsOf(page);

		await page.goto('/nope');

		await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
		await expect(page.locator('main p')).toHaveText(words['error.notFoundExplanation']);
		await expect(page).toHaveTitle(words['error.notFoundTitle']);
	});

	test('a failed read shows the header, the status number and the wordmark title, no sentence', async ({
		page
	}) => {
		const words = await wordsOf(page);
		await page.route(`**${SETTINGS_API}`, (route) => route.fulfill({ status: 500 }));

		await page.goto(SETTINGS_ADDRESS);

		await expect(page.locator('header.top .wordmark')).toHaveText(words['app.name']);
		await expect(page.locator('main h1')).toHaveText('500');
		await expect(page.locator('main p')).toHaveCount(0);
		await expect(page).toHaveTitle(words['app.name']);
	});
});

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`a member at Settings, ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'user' }, colorScheme });

			// LB4.
			test('meets the for-admins board inside the lobby over a 200 shell', async ({
				page
			}, testInfo) => {
				const words = await wordsOf(page);
				await page.setViewportSize({ width, height: 900 });

				const shell = await page.goto(SETTINGS_ADDRESS);

				expect(shell?.status()).toBe(200);
				expect((await page.request.get(SETTINGS_API, { headers: JSON_ACCEPT })).status()).toBe(403);
				await expect(page.locator('main h1')).toHaveText(words['error.adminsTitle']);
				await expect(page.locator('main p')).toHaveText(words['error.adminsExplanation']);
				await expect(page).toHaveTitle(words['error.adminsTitle']);
				await expect(page.getByRole('link', { name: words['settings.navLabel'] })).toHaveCount(0);
				await expect(page.getByRole('navigation', { name: words['settings.navLabel'] })).toHaveCount(0);
				await expect(page.locator('header.top button.account')).toBeVisible();
				await expectsNoHorizontalScroll(page);
				await page.screenshot({
					path: testInfo.outputPath(`for-admins-${width}-${colorScheme}.png`),
					fullPage: true
				});
			});
		});
	}
}

test('a signed-out /settings is sent to the sign-in with next set to /settings', async ({
	page
}) => {
	const started = startsSignIn(page, '%2Fsettings');

	await page.goto(SETTINGS_ADDRESS).catch(() => undefined);

	await leftForSignIn(page, started);
});

// Words and ages follow the language the server serves, not the browser's.
const secondsAgo = (seconds: number): string =>
	new Date(Date.parse(NOW) - seconds * 1000).toISOString();
const DAY = 24 * 60 * 60 * 1000;
const agedDecks: DeckRecord[] = [
	{
		state: 'never-built',
		title: 'Jahresrückblick 2025',
		changed_at: minutesAgo(6 * 24 * 60)
	},
	{
		state: 'ready',
		title: 'Kundenfeedback Q3',
		changed_at: minutesAgo(180),
		built_at: minutesAgo(12)
	},
	{
		state: 'failed',
		title: 'Knowledge Fabric',
		changed_at: minutesAgo(26),
		attempt: { started_at: minutesAgo(26), failure: { reason: 'no-words' } }
	},
	{
		state: 'building',
		title: 'Agenten-Fabrik',
		changed_at: minutesAgo(2),
		attempt: { started_at: secondsAgo(40) }
	}
];

test.describe('an instance in the second language', () => {
	test.use({
		person: { role: 'admin', source: OWN_SOURCE, decks: agedDecks },
		locale: 'en-US'
	});

	test('words the list, the deck page and the source page in it, with no reload', async ({
		page,
		signedIn,
		arrangeBaseline
	}) => {
		const settings = await arrangeBaseline();
		// The expected ages are the written language's own, never the browser's en-US.
		const second = sourceLanguage;
		const put = await page.request.put(SETTINGS_API, {
			headers: SAME_ORIGIN,
			data: {
				name: settings.name,
				language_tag: second
			}
		});
		expect(put.status()).toBe(200);
		try {
			const words = await wordsOf(page);
			await page.clock.setFixedTime(NOW);

			await page.goto('/');
			await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

			await expect(page.locator('html')).toHaveAttribute('lang', second);
			await expect(page.locator('table.decks tbody tr .changed')).toHaveText([
				'vor 2 Minuten',
				'vor 26 Minuten',
				'vor 3 Stunden',
				'vor 6 Tagen'
			]);
			const slugs = signedIn?.slugs ?? [];
			await page.locator('table.decks tbody tr', { hasText: slugs[1] }).getByRole('link').click();
			await expect(page.locator('.meta')).toContainText(words['deck.built']({ age: 'vor 12 Minuten' }));
			expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
			await page.goto(`/deck/${slugs[0]}`);
			await expect(page.locator('h1')).toHaveText(agedDecks[0]?.title ?? '');
			await page.goto(`/deck/${slugs[3]}`);
			await expect(page.locator('aside.banner')).toContainText(
				words['deck.buildingFor']({ age: '40 Sekunden' })
			);
			await page.goto(`/deck/${slugs[2]}`);
			await expect(page.locator('aside.banner')).toContainText('vor 26 Minuten');

			const name = await ownSourceName(page, OWN_SOURCE);
			await answersWith(page, name, (view) => ({
				...view,
				fetched_at: new Date(Date.parse(NOW) - 3 * DAY).toISOString()
			}));
			await page.goto(sourceAddress(name));
			await expect(page.locator('.meta')).toContainText('vor 3 Tagen');
		} finally {
			await arrangeBaseline(settings.name);
		}
	});
});
