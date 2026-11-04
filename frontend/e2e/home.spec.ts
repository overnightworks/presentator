
import { addDeck, type DeckRecord, expect, type Person, test } from './person.ts';
import {
	chooseTheme,
	entriesOf,
	endsTheSession,
	expectsHeaderOnOneLine,
	expectsNoHorizontalScroll,
	leftForSignIn,
	MENU,
	minutesAgo,
	NOW,
	opensTheMenu,
	sessionOf,
	SIGN_IN_START,
	startsSignIn,
	tabbedControlWithRing
} from './scenario.ts';

const SOURCE_ADDRESS = 'git@heimserver:decks.git';
const aDeck = (overrides: Partial<DeckRecord> = {}): DeckRecord => ({
	state: 'ready',
	title: 'Kundenfeedback Q3',
	changed_at: minutesAgo(5),
	...overrides
});

// Cell 1.
test('a signed-out / is sent to the sign-in with next set to /', async ({ page }) => {
	const started = startsSignIn(page);

	await page.goto('/').catch(() => undefined);

	await leftForSignIn(page, started);
});

const fourStates = [
	aDeck({
		state: 'building',
		title: 'Kundenfeedback Q3',
		changed_at: minutesAgo(2)
	}),
	aDeck({
		state: 'failed',
		title: 'Knowledge Fabric',
		changed_at: minutesAgo(26)
	}),
	aDeck({
		state: 'ready',
		title: 'Agenten-Fabrik',
		changed_at: minutesAgo(180)
	}),
	aDeck({
		state: 'never-built',
		title: 'Jahresrückblick 2025',
		changed_at: minutesAgo(6 * 24 * 60)
	})
];

// Cell 2: the browser words the ages, in the person's language, not its own.
const secondLanguageAges = ['vor 2 Minuten', 'vor 26 Minuten', 'vor 3 Stunden', 'vor 6 Tagen'];

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`the deck list at ${width} px, ${colorScheme}`, () => {
			test.use({
				person: {
					role: 'admin',
					language: 'second',
					decks: [...fourStates].reverse()
				},
				locale: 'en-US',
				colorScheme
			});

			test('lists four states newest first, their ages in the second language, and saves a screenshot', async ({
				page,
				signedIn
			}, testInfo) => {
				const { words } = await sessionOf(page);
				await page.clock.setFixedTime(NOW);
				await page.setViewportSize({ width, height: 900 });

				await page.goto('/');

				const rows = page.locator('table.decks tbody tr');
				await expect(rows).toHaveCount(4);
				const states = [
					words.deck_state_building,
					words.deck_state_failed,
					words.deck_state_ready,
					words.deck_state_never_built
				];
				for (const [index, deck] of fourStates.entries()) {
					const row = rows.nth(index);
					await expect(row.locator('.state')).toHaveText(states[index] ?? '');
					await expect(row.locator('.state')).toHaveAttribute('data-state', deck.state);
					await expect(row.locator('a')).toHaveText(deck.title);
					await expect(row.locator('a')).toHaveCSS('text-transform', 'none');
					await expect(row.locator('code')).toHaveText(
						signedIn?.slugs[fourStates.length - 1 - index] ?? ''
					);
					await expect(row.locator('.changed')).toHaveText(secondLanguageAges[index] ?? '');
				}
				await expect(page.locator('html')).toHaveAttribute('lang', words.language_tag);
				await page.evaluate(() => document.fonts.ready);

				await page.screenshot({
					path: testInfo.outputPath(`home-${width}-${colorScheme}.png`),
					fullPage: true
				});
			});
		});
	}
}

// Cell 3.
const emptyCards: { role: Person['role']; source: string | undefined }[] = [
	{ role: 'admin', source: undefined },
	{ role: 'admin', source: SOURCE_ADDRESS },
	{ role: 'user', source: undefined },
	{ role: 'user', source: SOURCE_ADDRESS }
];

for (const { role, source } of emptyCards) {
	test.describe(`the empty list of a ${role} ${source ? 'with' : 'without'} a source`, () => {
		test.use({ person: { role, source } });

		test('names what absence means, the explanation a link for an admin only, the address whenever set', async ({
			page
		}) => {
			const { words } = await sessionOf(page);

			await page.goto('/');

			await expect(page.getByRole('heading', { name: words.decks_empty_title })).toBeVisible();
			await expect(page.locator('table')).toHaveCount(0);
			// An empty list offers no way to add a deck or a source.
			await expect(page.locator('main input, main button')).toHaveCount(0);
			await expect(page.getByRole('link', { name: words.decks_empty_explanation })).toHaveCount(
				role === 'admin' ? 1 : 0
			);
			await expect(page.getByText(words.decks_empty_explanation)).toBeVisible();
			const address = page.locator('.empty code');
			if (source) {
				await expect(address).toHaveText(source);
			} else {
				await expect(address).toHaveCount(0);
			}
		});
	});
}

test.describe('sentence 9', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	// Cell 4.
	test('keeps the header on one line at 390 px with no horizontal scroll', async ({ page }) => {
		await page.setViewportSize({ width: 390, height: 844 });

		await page.goto('/');

		await expectsHeaderOnOneLine(page);
		await expectsNoHorizontalScroll(page);
	});

	test('shows a focus ring on each control and marks Decks as the current page', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await page.goto('/');
		await expect(page.getByRole('link', { name: words.section_decks })).toHaveAttribute(
			'aria-current',
			'page'
		);

		const focused: string[] = [];
		for (let tab = 0; tab < 7; tab++) {
			const control = await tabbedControlWithRing(page);
			if (control) {
				focused.push(control);
			}
		}
		expect(focused.length).toBeGreaterThanOrEqual(5);
	});

	test('shows a focus ring on every control of the open menu', async ({ page }) => {
		const { person } = await sessionOf(page);
		await page.goto('/');
		await opensTheMenu(page);

		const focused: string[] = [];
		for (let tab = 0; tab < 12 && !focused.includes('Log out'); tab++) {
			const control = await tabbedControlWithRing(page);
			if (control) {
				focused.push(control);
			}
		}

		expect(focused).toContain(person?.username);
		expect(focused).toContain('Log out');
		expect(focused.length).toBeGreaterThanOrEqual(3);
		expect(focused.some((control) => ['Follow system', 'Light', 'Dark'].includes(control))).toBe(true);
	});

	test('closes the menu on Escape and returns focus to its button; a click outside leaves focus where it landed', async ({
		page
	}) => {
		await page.goto('/');
		const button = page.locator('button.account');

		await opensTheMenu(page);
		await page.keyboard.press('Escape');
		await expect(page.locator(MENU)).toBeHidden();
		await expect(button).toBeFocused();

		await opensTheMenu(page);
		await page.getByRole('heading', { level: 1 }).click();
		await expect(page.locator(MENU)).toBeHidden();
		await expect(button).not.toBeFocused();
	});
});

// Cell 5.
test.describe('the theme', () => {
	test.use({ person: { role: 'admin', language: 'second' } });

	test('turns dark with no navigation and no history entry, and a reload keeps it', async ({
		page
	}) => {
		await page.goto('/');
		const entries = await entriesOf(page);
		// A document navigation would drop this mark.
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

		await chooseTheme(page, 'Dark');

		await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		expect(await entriesOf(page)).toBe(entries);
		await expect.poll(async () => (await sessionOf(page)).theme).toBe('dark');
		await page.reload();
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
	});

	test('shows on a Jinja page, keeps the stored language, and follows the device again', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		const backgroundOf = () =>
			page.locator('body').evaluate((body) => getComputedStyle(body).backgroundColor);
		await page.goto('/');
		await chooseTheme(page, 'Light');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
		const lightBackground = await backgroundOf();
		await page.keyboard.press('Escape');
		await chooseTheme(page, 'Dark');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
		const darkBackground = await backgroundOf();
		expect(darkBackground).not.toBe(lightBackground);
		await expect.poll(async () => (await sessionOf(page)).theme).toBe('dark');

		await page.goto('/settings/sources');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
		const account = await page.request.get('/api/account', {
			headers: { Accept: 'application/json' }
		});
		expect(((await account.json()) as { language_tag: string }).language_tag).toBe(
			words.language_tag
		);

		await page.goto('/');
		await chooseTheme(page, 'Follow system');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
		await page.emulateMedia({ colorScheme: 'dark' });
		await expect(page.locator('body')).toHaveCSS('background-color', darkBackground);
		await page.emulateMedia({ colorScheme: 'light' });
		await expect(page.locator('body')).toHaveCSS('background-color', lightBackground);
	});

	test('snaps the radio back when the write is refused', async ({ page }) => {
		await page.route('**/api/account', (route) =>
			route.request().method() === 'PUT' ? route.fulfill({ status: 500 }) : route.continue()
		);
		await page.goto('/');

		await chooseTheme(page, 'Dark');

		await expect(page.getByRole('radio', { name: 'Dark' })).not.toBeChecked();
		await expect(page.getByRole('radio', { name: 'Follow system' })).toBeChecked();
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'follow-system');
	});
});

// Cell 6.
test.describe('sentence 6', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	test('an ended session meets the sign-in when the theme changes', async ({ page, context }) => {
		const { words } = await sessionOf(page);
		await page.goto('/');
		await expect(page.getByRole('heading', { name: words.decks_title })).toBeVisible();
		await endsTheSession(context);
		const started = startsSignIn(page);

		await chooseTheme(page, 'Dark');

		await leftForSignIn(page, started);
	});

	test('an ended session meets the sign-in on the Decks link, the client-side load', async ({
		page,
		context
	}) => {
		const { words } = await sessionOf(page);
		await page.goto('/');
		await expect(page.getByRole('heading', { name: words.decks_title })).toBeVisible();
		await endsTheSession(context);
		const started = startsSignIn(page);

		await page.getByRole('link', { name: words.section_decks }).click();

		await leftForSignIn(page, started);
	});
});

// Cell 7.
test.describe('sentence 4', () => {
	test.use({ person: { role: 'admin', decks: [aDeck()] } });

	test('walks back and forward across Svelte, Jinja, Svelte and a refusal board', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		const showsHome = () =>
			expect(page.getByRole('heading', { name: words.decks_title })).toBeVisible();
		const showsSources = () => expect(page).toHaveURL(/\/settings\/sources$/);

		await page.goto('/');
		await showsHome();
		await page.goto('/settings/sources');
		await showsSources();
		await page.goto('/');
		await showsHome();
		await page.goto('/login');
		await expect(page).toHaveURL(/\/login$/);

		await page.goBack();
		await showsHome();
		await page.goBack();
		await showsSources();
		await page.goForward();
		await showsHome();
		await page.goForward();
		await expect(page).toHaveURL(/\/login$/);
	});

	test('the Decks link and a theme change add no entry, and the link shows a deck added meanwhile', async ({
		page,
		context,
		signedIn
	}) => {
		const { words } = await sessionOf(page);
		await page.goto('/');
		await expect(page.locator('table.decks tbody tr')).toHaveCount(1);
		const entries = await entriesOf(page);
		await addDeck(
			context,
			signedIn?.personId ?? '',
			aDeck({
				title: 'Added meanwhile',
				changed_at: minutesAgo(1)
			})
		);

		await page.getByRole('link', { name: words.section_decks }).click();

		await expect(page.locator('table.decks tbody tr')).toHaveCount(2);
		await expect(page.getByRole('link', { name: 'Added meanwhile' })).toBeVisible();
		await chooseTheme(page, 'Dark');
		await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
		expect(await entriesOf(page)).toBe(entries);
	});
});

test.describe('the empty card of an admin', () => {
	test.use({ person: { role: 'admin' } });

	test('links Sources by a document navigation', async ({ page }) => {
		const { words } = await sessionOf(page);
		await page.goto('/');

		const [answer] = await Promise.all([
			page.waitForResponse((received) => new URL(received.url()).pathname === '/settings/sources'),
			page.getByRole('link', { name: words.decks_empty_explanation }).click()
		]);

		expect(answer.request().isNavigationRequest()).toBe(true);
		expect(answer.status()).toBe(200);
		await expect(page.locator('#svelte-announcer')).toHaveCount(0);
	});
});

// Cell 9.
test.describe('log out', () => {
	test.use({ person: { role: 'user' } });

	test('posts to /logout and the next / is sent to the sign-in', async ({
		page,
		violations,
		browserName
	}) => {
		await page.goto('/');
		await opensTheMenu(page);

		const [posted] = await Promise.all([
			page.waitForRequest((sent) => new URL(sent.url()).pathname === '/logout'),
			page.getByRole('button', { name: 'Log out' }).click()
		]);
		await posted.response();

		expect(posted.method()).toBe('POST');
		// The library's logout page carries a policy of its own, under which
		// Firefox's request for a favicon is refused; that one violation is this
		// page's alone to excuse, and no other is.
		if (browserName === 'firefox') {
			await expect.poll(() => violations.some((one) => one.includes('favicon.ico'))).toBe(true);
		}
		violations.splice(
			0,
			violations.length,
			...violations.filter((one) => !one.includes('favicon.ico'))
		);
		const next = await page.request.get('/', {
			headers: { Accept: 'text/html' },
			maxRedirects: 0
		});
		expect(next.status()).toBe(302);
		expect(next.headers()['location']).toBe(`${SIGN_IN_START}?next=%2F`);
		// The logout page goes on to the issuer, which nobody answers in the drive.
		await page.goto('about:blank').catch(() => undefined);
	});
});

// Cell 10.
test.describe('a member', () => {
	test.use({ person: { role: 'user' } });

	test('sees no Settings link', async ({ page }) => {
		const { words } = await sessionOf(page);

		await page.goto('/');

		await expect(page.getByRole('link', { name: words.section_decks })).toBeVisible();
		await expect(page.getByRole('link', { name: words.section_settings })).toHaveCount(0);
	});
});

// Cell 11.
test.describe('the account menu', () => {
	test.use({ person: { role: 'admin' } });

	test('opens the account address in a new tab and carries the legal rows beside the footer', async ({
		page,
		context
	}) => {
		const { words, sign_in: addresses, person } = await sessionOf(page);
		await context.route(addresses.account_console_url, (route) =>
			route.fulfill({ contentType: 'text/plain', body: 'the central account' })
		);
		await page.goto('/');
		await opensTheMenu(page);

		const [popup] = await Promise.all([
			context.waitForEvent('page'),
			page.locator(MENU).getByRole('link', { name: person?.username }).click()
		]);

		await popup.waitForLoadState();
		expect(popup.url()).toBe(addresses.account_console_url);
		await expect(popup.locator('body')).toHaveText('the central account');
		await expect(page.locator(MENU)).toBeHidden();
		await opensTheMenu(page);
		for (const label of [words.footer_impressum, words.footer_datenschutz]) {
			await expect(page.getByRole('link', { name: label })).toHaveCount(2);
			await expect(page.locator(MENU).getByRole('link', { name: label })).toBeVisible();
			await expect(page.locator('footer').getByRole('link', { name: label })).toBeVisible();
		}
	});
});
