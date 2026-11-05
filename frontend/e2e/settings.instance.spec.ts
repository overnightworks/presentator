import type { Page } from '@playwright/test';

import type { SettingsView, SettingsWrite } from '../src/lib/api/settings.ts';
import { type DeckRecord, expect, signIn, test as base } from './person.ts';
import {
	chooseTheme,
	endsTheSession,
	entriesOf,
	expectsHeaderOnOneLine,
	expectsNoHorizontalScroll,
	leftForSignIn,
	minutesAgo,
	NOW,
	sessionOf,
	startsSignIn,
	tabbedControlWithRing
} from './scenario.ts';
import { answersWith, OWN_SOURCE, ownSourceName, sourceAddress } from './sources.ts';

// The instance defaults are one store per stack, so every cell that asserts a
// stored value or a repaint first arranges its own baseline through the API.
const JSON_ACCEPT = { Accept: 'application/json' };
const SAME_ORIGIN = { ...JSON_ACCEPT, 'Sec-Fetch-Site': 'same-origin' };
const SETTINGS_ADDRESS = '/settings';
const SETTINGS_API = '/api/settings';
const LANGUAGES_ROUTE = '/drive/languages';

async function storedSettings(page: Page): Promise<SettingsView> {
	const answer = await page.request.get(SETTINGS_API, { headers: JSON_ACCEPT });
	expect(answer.status()).toBe(200);
	return (await answer.json()) as SettingsView;
}

// The baseline speaks the language the driven lobby starts with; the harness
// names it, so no cell restates a tag.
const test = base.extend<{
	arrangeBaseline: (name?: string) => Promise<SettingsView>;
}>({
	arrangeBaseline: async ({ page }, use) => {
		await use(async (name = 'Heim') => {
			const languages = await page.request.get(LANGUAGES_ROUTE);
			const { first } = (await languages.json()) as { first: string };
			const baseline: SettingsWrite = {
				name,
				language_tag: first
			};
			const answer = await page.request.put(SETTINGS_API, {
				headers: SAME_ORIGIN,
				data: baseline
			});
			expect(answer.status()).toBe(200);
			return storedSettings(page);
		});
	}
});

// The language the baseline does not speak, by its tag and the name the select offers.
function otherLanguage(settings: SettingsView): { tag: string; name: string } {
	const other = settings.languages.find((language) => language.tag !== settings.language_tag);
	expect(other).toBeDefined();
	return other as { tag: string; name: string };
}

// Cell 1.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`Settings General at ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'admin' }, colorScheme });

			test('shows the tab strip, the three fields, the hint and Save, each with a ring', async ({
				page,
				arrangeBaseline
			}, testInfo) => {
				const settings = await arrangeBaseline();
				const { words } = await sessionOf(page);
				await page.setViewportSize({ width, height: 900 });

				await page.goto(SETTINGS_ADDRESS);

				const tabs = page.getByRole('navigation', { name: words.section_settings });
				await expect(tabs.getByRole('link', { name: words.settings_title })).toHaveAttribute(
					'aria-current',
					'page'
				);
				await expect(tabs.getByRole('link', { name: words.settings_sources })).not.toHaveAttribute(
					'aria-current'
				);
				await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
				await expect(page.getByLabel(words.settings_instance_name)).toHaveValue(settings.name);
				const language = page.getByLabel(words.settings_default_language);
				await expect(language.locator('option')).toHaveText(
					settings.languages.map((offered) => offered.name)
				);
				await expect(language.locator('option:checked')).toHaveText(
					settings.languages.find((offered) => offered.tag === settings.language_tag)?.name ?? ''
				);
				await expect(page.getByRole('button', { name: words.settings_save })).toBeEnabled();
				const hint = page.getByRole('button', { name: words.settings_language_hint });
				await hint.click();
				await expect(page.getByText(words.settings_language_hint, { exact: true })).toBeVisible();
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
						words.settings_instance_name,
						settings.languages.map((offered) => offered.name).join(''),
						'info',
						words.settings_save
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

	// Cell 2.
	test('Settings is a client navigation, current beside Decks, and back and forward land right', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		const documents: string[] = [];
		page.on('request', (sent) => {
			if (sent.isNavigationRequest()) {
				documents.push(new URL(sent.url()).pathname);
			}
		});
		await page.goto('/');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.decks_title);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
		documents.length = 0;

		await page.getByRole('link', { name: words.section_settings }).click();

		await expect(page).toHaveURL(/\/settings$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
		await expect(page.getByRole('link', { name: words.section_settings })).toHaveAttribute(
			'aria-current',
			'page'
		);
		await expect(page.getByRole('link', { name: words.section_decks })).not.toHaveAttribute(
			'aria-current'
		);
		await page.goBack();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.decks_title);
		await page.goForward();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
		expect(documents).toEqual([]);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
	});

	// Cell 3.
	test('Sources is a client navigation to the list, and its General link comes back', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await page.getByRole('link', { name: words.settings_sources }).click();

		await expect(page).toHaveURL(/\/settings\/sources$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_sources);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		await page.getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL(/\/settings$/);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
	});

	// Cell 4.
	test('Save repaints the same document with the stored answer and a reload keeps it', async ({
		page,
		browser,
		arrangeBaseline
	}, testInfo) => {
		const settings = await arrangeBaseline();
		const before = await sessionOf(page);
		const second = otherLanguage(settings);
		await page.goto(SETTINGS_ADDRESS);
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
		const entries = await entriesOf(page);
		// A document navigation would drop this mark.
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await page.getByLabel(before.words.settings_instance_name).fill('Hafen');
		await page.getByLabel(before.words.settings_default_language).selectOption(second.tag);
		await page.getByRole('button', { name: before.words.settings_save }).click();

		await expect(page.locator('html')).toHaveAttribute('lang', second.tag);
		const after = await sessionOf(page);
		expect(after.words.language_tag).toBe(second.tag);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(after.words.settings_title);
		await expect(page.getByLabel(after.words.settings_instance_name)).toHaveValue('Hafen');
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		expect(await entriesOf(page)).toBe(entries);
		await page.reload();
		await expect(page.getByLabel(after.words.settings_instance_name)).toHaveValue('Hafen');

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

	test('Saving the name leaves an admin whose own theme is Light on Light', async ({
		page,
		arrangeBaseline
	}) => {
		await arrangeBaseline();
		const { words } = await sessionOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await chooseTheme(page, 'Light');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
		await page.keyboard.press('Escape');

		await page.getByLabel(words.settings_instance_name).fill('Hafen');
		const sessionRead = page.waitForResponse(
			(received) =>
				new URL(received.url()).pathname === '/api/session' && received.request().method() === 'GET'
		);
		await page.getByRole('button', { name: words.settings_save }).click();

		await expect.poll(async () => (await storedSettings(page)).name).toBe('Hafen');
		await sessionRead;
		await expect(page.getByRole('button', { name: words.settings_save })).toBeEnabled();
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
	});

	// Cell 5.
	test('Enter in the name field saves', async ({ page, arrangeBaseline }) => {
		await arrangeBaseline();
		const { words } = await sessionOf(page);
		await page.goto(SETTINGS_ADDRESS);

		await page.getByLabel(words.settings_instance_name).fill('Hafen');
		await page.getByLabel(words.settings_instance_name).press('Enter');

		await expect.poll(async () => (await storedSettings(page)).name).toBe('Hafen');
	});

	// Cell 6.
	test('a failed write repaints nothing, enables Save again and keeps what was typed', async ({
		page,
		arrangeBaseline
	}) => {
		const baseline = await arrangeBaseline();
		const { words } = await sessionOf(page);
		const errors: Error[] = [];
		page.on('pageerror', (error) => errors.push(error));
		await page.route(`**${SETTINGS_API}`, (route) =>
			route.request().method() === 'PUT' ? route.fulfill({ status: 500 }) : route.continue()
		);
		await page.goto(SETTINGS_ADDRESS);
		await page.getByLabel(words.settings_instance_name).fill('Hafen');

		await page.getByRole('button', { name: words.settings_save }).click();

		await expect.poll(() => errors.length).toBe(1);
		await expect(page.getByRole('button', { name: words.settings_save })).toBeEnabled();
		await expect(page.getByLabel(words.settings_instance_name)).toHaveValue('Hafen');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
		await page.unroute(`**${SETTINGS_API}`);
		expect(await storedSettings(page)).toEqual(baseline);
	});

	// Cell 7.
	test('an ended session meets the sign-in when Save is pressed', async ({ page, context }) => {
		const { words } = await sessionOf(page);
		await page.goto(SETTINGS_ADDRESS);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
		await endsTheSession(context);
		const started = startsSignIn(page, '%2Fsettings');

		await page.getByRole('button', { name: words.settings_save }).click();

		await leftForSignIn(page, started);
	});

	test('an ended session meets the sign-in on the Settings link, naming /settings', async ({
		page,
		context
	}) => {
		const { words } = await sessionOf(page);
		await page.goto('/');
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.decks_title);
		await endsTheSession(context);
		const started = startsSignIn(page, '%2Fsettings');

		await page.getByRole('link', { name: words.section_settings }).click();

		await leftForSignIn(page, started);
	});

	// Cell 9, the unknown address and a failed read.
	test('an unknown address keeps its words and title', async ({ page }) => {
		const { words } = await sessionOf(page);

		await page.goto('/nope');

		await expect(page.locator('main h1')).toHaveText(words.address_unknown_title);
		await expect(page.locator('main p')).toHaveText(words.address_unknown_explanation);
		await expect(page).toHaveTitle(words.address_unknown_title);
	});

	test('a failed read shows the header, the status number and the wordmark title, no sentence', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await page.route(`**${SETTINGS_API}`, (route) => route.fulfill({ status: 500 }));

		await page.goto(SETTINGS_ADDRESS);

		await expect(page.locator('header.top .wordmark')).toHaveText(words.wordmark);
		await expect(page.locator('main h1')).toHaveText('500');
		await expect(page.locator('main p')).toHaveCount(0);
		await expect(page).toHaveTitle(words.wordmark);
	});
});

// Cell 8.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`a member at Settings, ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'user' }, colorScheme });

			test('meets the for-admins board inside the lobby over a 200 shell', async ({
				page
			}, testInfo) => {
				const { words } = await sessionOf(page);
				await page.setViewportSize({ width, height: 900 });

				const shell = await page.goto(SETTINGS_ADDRESS);

				expect(shell?.status()).toBe(200);
				expect((await page.request.get(SETTINGS_API, { headers: JSON_ACCEPT })).status()).toBe(403);
				await expect(page.locator('main h1')).toHaveText(words.for_admins_title);
				await expect(page.locator('main p')).toHaveText(words.for_admins_explanation);
				await expect(page).toHaveTitle(words.for_admins_title);
				await expect(page.getByRole('link', { name: words.section_settings })).toHaveCount(0);
				await expect(page.getByRole('navigation', { name: words.section_settings })).toHaveCount(0);
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

// Cell 10.
test('a signed-out /settings is sent to the sign-in with next set to /settings', async ({
	page
}) => {
	const started = startsSignIn(page, '%2Fsettings');

	await page.goto(SETTINGS_ADDRESS).catch(() => undefined);

	await leftForSignIn(page, started);
});

// Cell 7: words and ages follow the language the server serves, not the browser's.
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
		const second = otherLanguage(settings);
		const put = await page.request.put(SETTINGS_API, {
			headers: SAME_ORIGIN,
			data: {
				name: settings.name,
				language_tag: second.tag
			}
		});
		expect(put.status()).toBe(200);
		try {
			const { words } = await sessionOf(page);
			const filled = (template: string, age: string): string => template.replace('{age}', age);
			await page.clock.setFixedTime(NOW);

			await page.goto('/');
			await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

			await expect(page.locator('html')).toHaveAttribute('lang', second.tag);
			await expect(page.locator('table.decks tbody tr .changed')).toHaveText([
				'vor 2 Minuten',
				'vor 26 Minuten',
				'vor 3 Stunden',
				'vor 6 Tagen'
			]);
			const slugs = signedIn?.slugs ?? [];
			await page.locator('table.decks tbody tr', { hasText: slugs[1] }).getByRole('link').click();
			await expect(page.locator('.meta')).toContainText(filled(words.deck_built, 'vor 12 Minuten'));
			expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
			await page.goto(`/deck/${slugs[0]}`);
			await expect(page.locator('h1')).toHaveText(agedDecks[0]?.title ?? '');
			await page.goto(`/deck/${slugs[3]}`);
			await expect(page.locator('aside.banner')).toContainText(
				filled(words.deck_building_for, '40 Sekunden')
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
