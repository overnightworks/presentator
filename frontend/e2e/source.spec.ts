import type { Locator, Page } from '@playwright/test';

import type { SourceView, WebhookSecretView } from '../src/lib/api/sources.ts';
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
	ACCESS_TOKEN,
	answersWith,
	givenADeployKey,
	JSON_ACCEPT,
	opensALocalSource,
	opensAnHttpsSource,
	OWN_SOURCE,
	ownSourceName,
	sourceAddress,
	SOURCES_API,
	shellAt,
	SOURCES_LIST,
	tabsOf,
	holdsRequests,
	type Words
} from './sources.ts';

const GONE = 'no-such-source-in-this-drive';
const NEXT_OF = (name: string): string => `%2Fsettings%2Fsources%2F${name}`;
const DAY = 24 * 60 * 60 * 1000;

// Playwright turns Chromium's back/forward cache off; the cache cell turns it on.
test.use({ launchOptions: { ignoreDefaultArgs: ['--disable-back-forward-cache'] } });

const section = (page: Page, heading: string): Locator =>
	page.locator('section.sec').filter({ has: page.getByRole('heading', { name: heading }) });

async function renewsTheWebhook(page: Page, words: Words, name: string): Promise<string> {
	const answered = page.waitForResponse(
		(received) =>
			received.url().endsWith(`${SOURCES_API}/${name}/webhook-secret`) &&
			received.request().method() === 'POST'
	);
	await section(page, words.source_webhook_heading)
		.getByRole('button', { name: words.source_renew, exact: true })
		.click();
	const answer = await answered;
	expect(answer.status()).toBe(200);
	const { webhook_secret: secret } = (await answer.json()) as WebhookSecretView;
	await expect(section(page, words.source_webhook_heading)).toContainText(secret);
	return secret;
}

const hookStatus = async (page: Page, hookUrl: string, secret: string): Promise<number> =>
	(await page.request.post(hookUrl, { headers: { Authorization: `Bearer ${secret}` } })).status();

const hookUrlOf = async (page: Page, name: string): Promise<string> =>
	((await (await page.request.get(`${SOURCES_API}/${name}`, { headers: JSON_ACCEPT })).json()) as SourceView)
		.hook_url;

const notInThePage = (page: Page, secret: string) =>
	expect(page.locator('body')).not.toContainText(secret);

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin', source: OWN_SOURCE } });

	// Cells 1 to 3: the three kinds of source at both widths and both schemes.
	const kinds = ['https', 'ssh', 'local'] as const;
	for (const kind of kinds) {
		for (const colorScheme of ['light', 'dark'] as const) {
			for (const width of [390, 1280]) {
				test.describe(`a ${kind} source at ${width} px, ${colorScheme}`, () => {
					test.use({ colorScheme });

					test('shows every part of its kind and saves a screenshot', async ({
						page
					}, testInfo) => {
						const { words } = await sessionOf(page);
						await page.setViewportSize({ width, height: 900 });
						let name: string;
						if (kind === 'ssh') {
							name = await ownSourceName(page, OWN_SOURCE);
							await givenADeployKey(page, name);
						} else if (kind === 'local') {
							({ name } = await opensALocalSource(page, words, testInfo));
						} else {
							({ name } = await opensAnHttpsSource(page, words, testInfo));
						}
						const shell = await page.goto(sourceAddress(name));

						expect(shell?.status()).toBe(200);
						await expect(page).toHaveTitle(name);
						await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
						const access = section(page, words.source_access_heading);
						const webhook = section(page, words.source_webhook_heading);
						const dots = page.getByText(words.source_secret_dots, { exact: true });
						if (kind === 'https') {
							await expect(access.locator('.tag')).toHaveText(words.source_access_token);
							await expect(access.locator('input[type="password"]')).toHaveCount(1);
							await expect(dots).toHaveCount(2);
							await expect(page.getByRole('heading', { name: words.source_decks })).toHaveCount(0);
						} else if (kind === 'ssh') {
							await expect(access.locator('.tag')).toHaveText(words.source_access_deploy_key);
							await expect(access.locator('input')).toHaveCount(0);
							await expect(access.getByRole('button', { name: words.source_copy })).toBeVisible();
							await expect(
								access.getByRole('button', { name: words.source_deploy_key_renew, exact: true })
							).toBeVisible();
							await expect(dots).toHaveCount(1);
						} else {
							await expect(access.locator('.tag')).toHaveText(words.source_access_local);
							await expect(access.locator('input')).toHaveCount(0);
							await expect(access.getByRole('button')).toHaveCount(0);
							await expect(dots).toHaveCount(1);
						}
						await expect(webhook.getByRole('group', { name: words.source_webhook_address })).toBeVisible();
						await expect(webhook.getByRole('button', { name: words.source_copy })).toBeVisible();
						await expect(page.locator('main form form')).toHaveCount(0);
						await expectsNoHorizontalScroll(page);
						if (width === 390) {
							await expectsHeaderOnOneLine(page);
						}
						await page.evaluate(() => document.fonts.ready);
						await page.screenshot({
							path: testInfo.outputPath(`source-${kind}-${width}-${colorScheme}.png`),
							fullPage: true
						});
					});
				});
			}
		}
	}

	test('names a folder in the header when the source has one and nothing when it has none', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		for (const width of [390, 1280]) {
			await page.setViewportSize({ width, height: 900 });
			await answersWith(page, name, (view) => ({ ...view, folder: 'examples' }));
			await page.goto(sourceAddress(name));
			await expect(page.locator('.meta')).toContainText(`${words.source_folder}examples`);
			await expectsNoHorizontalScroll(page);
			await page.unroute(`**${SOURCES_API}/${name}`);

			await answersWith(page, name, (view) => ({ ...view, folder: '' }));
			await page.goto(sourceAddress(name));
			await expect(page.locator('.meta')).not.toContainText(words.source_folder);
			await page.unroute(`**${SOURCES_API}/${name}`);
		}
	});

	// Cell 4.
	test('words every state, run, commit and failure of the answer, the null access and the missing key', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const at = new Date().toISOString();
		const runs = (['unreachable', 'refused', 'failed', 'credential-unresolvable'] as const).map(
			(reason) => ({
				at,
				commit: null,
				outcome: 'failure' as const,
				reason,
				state: 'error' as const
			})
		);
		await answersWith(page, name, (view) => ({
			...view,
			state: 'error',
			runs: [
				{ at, commit: 'a3f19c2', outcome: 'success', reason: null, state: 'reachable' },
				...runs,
				{ at, commit: null, outcome: 'failure', reason: null, state: 'error' }
			]
		}));
		await page.goto(sourceAddress(name));

		const table = page.locator('table.runs');
		await expect(table.locator('tbody tr')).toHaveCount(6);
		await expect(table.locator('tbody tr').first()).toContainText(words.source_run_fetched);
		await expect(table.locator('tbody tr').first().locator('samp')).toHaveText('a3f19c2');
		for (const sentence of [
			words.source_run_unreachable,
			words.source_run_refused,
			words.source_run_failed,
			words.source_run_secret
		]) {
			await expect(table).toContainText(sentence);
		}
		await expect(page.locator('.meta .state')).toHaveText(words.source_state_error);
		await page.unroute(`**${SOURCES_API}/${name}`);

		for (const [state, word] of [
			['reachable', words.source_state_reachable],
			['refused', words.source_state_refused],
			['failed', words.source_state_failed],
			['never-fetched', words.source_state_never_fetched]
		] as const) {
			await answersWith(page, name, (view) => ({
				...view,
				state,
				fetched_at: state === 'never-fetched' ? null : view.fetched_at,
				runs: []
			}));
			await page.goto(sourceAddress(name));
			await expect(page.locator('.meta .state')).toHaveText(word);
			await expect(page.locator('table.runs')).toHaveCount(0);
			if (state === 'never-fetched') {
				await expect(page.locator('.meta')).toContainText('—');
			}
			await page.unroute(`**${SOURCES_API}/${name}`);
		}

		await answersWith(page, name, (view) => ({ ...view, access: null }));
		await page.goto(sourceAddress(name));
		const access = section(page, words.source_access_heading);
		await expect(access.locator('.tag')).toHaveCount(0);
		await expect(access.locator('input[type="password"]')).toHaveCount(1);
		await page.unroute(`**${SOURCES_API}/${name}`);

		const own = await ownSourceName(page, OWN_SOURCE);
		await givenADeployKey(page, own);
		await answersWith(page, own, (view) => ({ ...view, public_key: null }));
		await page.goto(sourceAddress(own));
		const ssh = section(page, words.source_access_heading);
		await expect(ssh.getByRole('button', { name: words.source_deploy_key_renew, exact: true })).toBeVisible();
		await expect(ssh.getByRole('button', { name: words.source_copy })).toHaveCount(0);
	});

	// Cell 6.
	test.describe('with decks of its own', () => {
		test.use({
			person: {
				role: 'admin',
				source: OWN_SOURCE,
				decks: [
					{
						state: 'ready',
						title: 'A deck of this source',
						changed_at: '2026-11-03T11:00:00Z',
						built_at: '2026-11-03T11:30:00Z'
					}
				]
			}
		});

		test('chips link to the deck page by client navigation and Back returns', async ({ page }) => {
			const name = await ownSourceName(page, OWN_SOURCE);
			await page.goto(sourceAddress(name));
			await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
			const chips = page.locator('.chips .chip');
			await expect(chips).toHaveCount(1);
			await expect(chips).toContainText('A deck of this source');

			await chips.click();

			await expect(page).toHaveURL(/\/deck\//);
			expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
			await page.goBack();
			await expect(page).toHaveURL(sourceAddress(name));
			await expect(chips).toHaveCount(1);
		});
	});

	// Cell 7.
	test('the back link and General are client navigations and Sources is the current tab', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
		const tabs = tabsOf(page, words);
		await expect(tabs.getByRole('link', { name: words.settings_sources })).toHaveAttribute(
			'aria-current',
			'page'
		);

		await tabs.getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL('/settings');
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		await page.goBack();
		await expect(page).toHaveURL(sourceAddress(name));
		const documents: string[] = [];
		page.on('request', (sent) => {
			if (sent.resourceType() === 'document') {
				documents.push(sent.url());
			}
		});
		await page.getByRole('link', { name: words.sources_back }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(tabs.getByRole('link', { name: words.settings_sources })).toHaveAttribute(
			'aria-current',
			'page'
		);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
		expect(documents).toEqual([]);
	});

	// Cell 8.
	test('an unknown source shows the not-found board inside the lobby', async ({ page }) => {
		const { words } = await sessionOf(page);

		await page.goto(sourceAddress(GONE));

		await expect(page.locator('main h1')).toHaveText(words.address_unknown_title);
		await expect(page.locator('header.top')).toBeVisible();
	});

	// Cell 11.
	test('with the session ended, Fetch now leaves for the sign-in once with this address as next', async ({
		page,
		context
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const signIns: string[] = [];
		page.on('request', (sent) => {
			if (new URL(sent.url()).pathname === SIGN_IN_START) {
				signIns.push(new URL(sent.url()).search);
			}
		});
		await endsTheSession(context);
		const started = startsSignIn(page, NEXT_OF(name));

		await page.getByRole('button', { name: words.sources_fetch_now }).click();

		await leftForSignIn(page, started);
		expect(signIns).toEqual([`?next=${NEXT_OF(name)}`]);
	});

	// Cell 12.
	test('Fetch now answers 200, re-reads the page, disables the buttons meanwhile and adds no entry', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const entries = await entriesOf(page);
		await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
		const held = await holdsRequests(page, `**${SOURCES_API}/${name}/fetch`);
		const answered = page.waitForResponse(
			(received) => received.url().endsWith(`${name}/fetch`) && received.status() === 200
		);
		const reread = page.waitForResponse(
			(received) =>
				received.url().endsWith(`${SOURCES_API}/${name}`) && received.request().method() === 'GET'
		);

		await page.getByRole('button', { name: words.sources_fetch_now }).click();

		const busy = page.getByRole('button', { name: words.source_busy_fetch });
		await expect(busy).toHaveAttribute('aria-busy', 'true');
		await expect(busy.locator('.state[data-state="building"]')).toBeVisible();
		await page.screenshot({ path: testInfo.outputPath('fetch-busy.png') });
		const buttons = page.locator('main button');
		expect(await buttons.count()).toBeGreaterThan(1);
		for (const button of await buttons.all()) {
			await expect(button).toBeDisabled();
		}
		held.release();
		await answered;
		await reread;
		await expect(page.getByRole('button', { name: words.sources_fetch_now })).toBeEnabled();
		expect(await entriesOf(page)).toBe(entries);
		expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
	});

	for (const path of ['access', 'webhook-secret']) {
		test(`a held ${path} renewal shows the busy word on its own button only`, async ({
			page
		}, testInfo) => {
			const { words } = await sessionOf(page);
			const { name } = await opensAnHttpsSource(page, words, testInfo);
			const held = await holdsRequests(page, `**${SOURCES_API}/${name}/${path}`);
			await page.getByLabel(words.source_secret, { exact: true }).fill(ACCESS_TOKEN);
			const heading = path === 'access' ? words.source_access_heading : words.source_webhook_heading;

			await section(page, heading)
				.getByRole('button', { name: words.source_renew, exact: true })
				.click();

			const busy = section(page, heading).getByRole('button', { name: words.source_busy_renew });
			await expect(busy).toHaveAttribute('aria-busy', 'true');
			await expect(busy.locator('.state[data-state="building"]')).toBeVisible();
			await expect(page.getByRole('button', { name: words.source_busy_renew })).toHaveCount(1);
			for (const button of await page.locator('main button').all()) {
				await expect(button).toBeDisabled();
			}
			held.release();
			await expect(
				section(page, heading).getByRole('button', { name: words.source_renew, exact: true })
			).toBeEnabled();
		});
	}

	// Cells 14 and 15.
	test('an HTTPS renewal clears the field, keeps the dots and leaves the token nowhere', async ({
		page,
		context
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const consoleLines: string[] = [];
		const requested: string[] = [];
		page.on('console', (message) => consoleLines.push(message.text()));
		page.on('request', (sent) => requested.push(sent.url()));
		const entries = await entriesOf(page);
		const field = page.getByLabel(words.source_secret, { exact: true });
		await field.fill(ACCESS_TOKEN);
		const answered = page.waitForResponse(
			(received) => received.url().endsWith(`${name}/access`) && received.status() === 204
		);

		await section(page, words.source_access_heading)
			.getByRole('button', { name: words.source_renew, exact: true })
			.click();
		await answered;

		await expect(field).toHaveValue('');
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(2);
		expect(await entriesOf(page)).toBe(entries);
		await expect(page.locator('body')).not.toContainText(ACCESS_TOKEN);
		const kept = await page.evaluate(() =>
			JSON.stringify([
				{ ...window.sessionStorage },
				{ ...window.localStorage },
				document.cookie,
				document.title,
				history.state,
				location.href,
				document.documentElement.outerHTML
			])
		);
		expect(kept).not.toContain(ACCESS_TOKEN);
		expect(JSON.stringify(await context.cookies())).not.toContain(ACCESS_TOKEN);
		expect(requested.join('\n')).not.toContain(ACCESS_TOKEN);
		expect(consoleLines.join('\n')).not.toContain(ACCESS_TOKEN);
	});

	test('spaces as the token show the refusal sentence and keep what was typed', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		await opensAnHttpsSource(page, words, testInfo);
		const field = page.getByLabel(words.source_secret, { exact: true });
		await field.fill('   ');

		await section(page, words.source_access_heading)
			.getByRole('button', { name: words.source_renew, exact: true })
			.click();

		await expect(page.getByRole('alert')).toHaveText(words.source_refused_secret);
		await expect(field).toHaveValue('   ');
	});

	// Cell 15b.
	test('a renewal answered 500 fails loud, keeps the typed token and enables the buttons again', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const failures: Error[] = [];
		page.on('pageerror', (failure) => failures.push(failure));
		await page.route(`**${SOURCES_API}/${name}/access`, (route) =>
			route.fulfill({ status: 500, json: {} })
		);
		const field = page.getByLabel(words.source_secret, { exact: true });
		await field.fill(ACCESS_TOKEN);

		await section(page, words.source_access_heading)
			.getByRole('button', { name: words.source_renew, exact: true })
			.click();

		await expect.poll(() => failures.length).toBe(1);
		await expect(field).toHaveValue(ACCESS_TOKEN);
		await expect(page.getByRole('button', { name: words.sources_fetch_now })).toBeEnabled();
	});

	// Cell 16.
	test('the SSH renewal shows a different public key', async ({ page }) => {
		const { words } = await sessionOf(page);
		const name = await ownSourceName(page, OWN_SOURCE);
		await givenADeployKey(page, name);
		await page.goto(sourceAddress(name));
		const key = section(page, words.source_access_heading).locator('.code code');
		const before = await key.textContent();

		await section(page, words.source_access_heading)
			.getByRole('button', { name: words.source_deploy_key_renew, exact: true })
			.click();

		await expect(key).not.toHaveText(before ?? '');
	});

	// Cells 17 and 18.
	test('a webhook renewal shows the secret once, a second replaces it and the old one stops working', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name, created } = await opensAnHttpsSource(page, words, testInfo);
		const hook = await hookUrlOf(page, name);
		const entries = await entriesOf(page);

		const first = await renewsTheWebhook(page, words, name);

		await expect(page.locator('.once')).toHaveText(words.source_webhook_once);
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(1);
		expect(await entriesOf(page)).toBe(entries);
		expect(await hookStatus(page, hook, created.webhook_secret)).toBe(404);
		expect(await hookStatus(page, hook, first)).toBe(204);
		const second = await renewsTheWebhook(page, words, name);
		await notInThePage(page, first);
		expect(await hookStatus(page, hook, first)).toBe(404);
		expect(await hookStatus(page, hook, second)).toBe(204);
	});

	// Cell 19.
	test('after a renewal the secret is found nowhere but the visible text', async ({
		page,
		context
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const consoleLines: string[] = [];
		const requested: string[] = [];
		page.on('console', (message) => consoleLines.push(message.text()));
		page.on('request', (sent) => requested.push(sent.url()));

		const secret = await renewsTheWebhook(page, words, name);

		const kept = await page.evaluate(async () =>
			JSON.stringify({
				session: { ...window.sessionStorage },
				local: { ...window.localStorage },
				cookie: document.cookie,
				title: document.title,
				state: history.state,
				address: location.href,
				performance: performance.getEntries().map((entry) => entry.name),
				attributes: [...document.querySelectorAll('*')].flatMap((element) =>
					element.getAttributeNames().map((each) => `${each}=${element.getAttribute(each)}`)
				)
			})
		);
		expect(kept).not.toContain(secret);
		// The page keeps nothing in these two, so empty is the whole proof.
		expect(await page.evaluate(() => indexedDB.databases())).toEqual([]);
		expect(await page.evaluate(() => caches.keys())).toEqual([]);
		expect(JSON.stringify(await context.cookies())).not.toContain(secret);
		expect(requested.join('\n')).not.toContain(secret);
		expect(consoleLines.join('\n')).not.toContain(secret);
		for (const path of [`${SOURCES_API}/${name}`, SOURCES_API, '/api/decks', '/api/session']) {
			const read = await page.request.get(path, { headers: JSON_ACCEPT });
			expect(await read.text(), path).not.toContain(secret);
		}
		const shell = await shellAt(page, sourceAddress(name));
		expect(shell.status()).toBe(200);
		expect(await shell.text()).not.toContain(secret);
		const other = await context.newPage();
		await other.goto(sourceAddress(name));
		await expect(other.locator('body')).toContainText(words.source_secret_dots);
		await notInThePage(other, secret);
		await other.close();
		await page.reload();
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(2);
		await notInThePage(page, secret);
	});

	// Cells 20 and 21.
	test('General then Back and Forward, and the back link then Back and Forward, never show it', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const secret = await renewsTheWebhook(page, words, name);

		await tabsOf(page, words).getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL('/settings');
		await page.goBack();
		await expect(page).toHaveURL(sourceAddress(name));
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(2);
		await notInThePage(page, secret);
		await page.goForward();
		await expect(page).toHaveURL('/settings');
		await notInThePage(page, secret);

		await page.goBack();
		const secondSecret = await renewsTheWebhook(page, words, name);
		await page.getByRole('link', { name: words.sources_back }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await page.goBack();
		await expect(page).toHaveURL(sourceAddress(name));
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(2);
		await notInThePage(page, secondSecret);
		await page.goForward();
		await expect(page).toHaveURL(SOURCES_LIST);
		await notInThePage(page, secondSecret);

		await page.goBack();
		const thirdSecret = await renewsTheWebhook(page, words, name);
		await page.goto(SOURCES_LIST);
		await page.goBack();
		await expect(page).toHaveURL(sourceAddress(name));
		await notInThePage(page, thirdSecret);
		await page.goForward();
		await expect(page).toHaveURL(SOURCES_LIST);
		await notInThePage(page, thirdSecret);
	});

	// Cell 22.
	test('hiding the page clears the typed token and the shown secret, both present before', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const field = page.getByLabel(words.source_secret, { exact: true });
		await field.fill(ACCESS_TOKEN);
		const secret = await renewsTheWebhook(page, words, name);
		await expect(field).toHaveValue(ACCESS_TOKEN);
		await expect(page.locator('body')).toContainText(secret);

		await page.evaluate(() => dispatchEvent(new Event('pagehide')));

		await expect(field).toHaveValue('');
		await notInThePage(page, secret);
		await expect(page.getByText(words.source_secret_dots, { exact: true })).toHaveCount(2);
	});

	// Cell 23, the back-forward cache, which Chromium keeps from a no-store page.
	test('restores no secret from the cache once the control page is restored', async ({
		page,
		browserName
	}, testInfo) => {
		test.skip(browserName !== 'chromium', 'only Chromium is driven for the cache');
		const { words } = await sessionOf(page);
		await page.addInitScript(() => {
			addEventListener('pageshow', (shown) => {
				if (shown.persisted) {
					Object.assign(window, { restoredFromTheCache: true });
				}
			});
		});
		await page.goto(SOURCES_LIST);
		await page.goto('/settings');
		await page.goBack();
		await expect(page).toHaveURL(SOURCES_LIST);
		const restored = await page.evaluate(() => 'restoredFromTheCache' in window);
		const why = await page.evaluate(() => {
			const entry = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming & {
				notRestoredReasons?: { reasons: { reason: string }[] };
			};
			return (entry.notRestoredReasons?.reasons ?? []).map((each) => each.reason).join(', ');
		});
		test.skip(!restored, `the control page is not restored from the cache: ${why}`);

		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const secret = await renewsTheWebhook(page, words, name);
		await page.goto(SOURCES_LIST);
		await page.goBack();

		expect(await page.evaluate(() => 'restoredFromTheCache' in window)).toBe(true);
		await notInThePage(page, secret);
	});

	// Cell 26.
	test('every control shows a focus ring at 390 px, in order, and the info opens by keyboard', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		await page.setViewportSize({ width: 390, height: 900 });
		await opensAnHttpsSource(page, words, testInfo);
		await page.locator('body').click({ position: { x: 1, y: 1 } });

		const focused: (string | null)[] = [];
		for (let tab = 0; tab < 14; tab++) {
			focused.push(await tabbedControlWithRing(page));
		}

		const controlsInOrder = [
			words.sources_back,
			words.sources_fetch_now,
			'info',
			words.source_secret,
			words.source_renew,
			words.source_copy,
			words.source_renew
		];
		let from = 0;
		for (const control of controlsInOrder) {
			const at = focused.indexOf(control, from);
			expect(at, `${control} in ${focused.join(' | ')}`).toBeGreaterThanOrEqual(0);
			from = at + 1;
		}
		await page.locator('button.info').focus();
		await page.keyboard.press('Enter');
		await expect(page.locator('#access-hint')).toBeVisible();
		await page.keyboard.press('Escape');
		await expect(page.locator('#access-hint')).toBeHidden();
	});

	// Cell 27.
	test('Copy puts the shown secret on the clipboard in Chromium and raises no error in Firefox', async ({
		page,
		context,
		browserName
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const failures: Error[] = [];
		page.on('pageerror', (failure) => failures.push(failure));
		if (browserName === 'chromium') {
			await context.grantPermissions(['clipboard-read', 'clipboard-write']);
		}
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const secret = await renewsTheWebhook(page, words, name);

		await section(page, words.source_webhook_heading)
			.locator('.code')
			.filter({ hasText: secret })
			.getByRole('button', { name: words.source_copy })
			.click();

		if (browserName === 'chromium') {
			await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(secret);
		}
		expect(failures).toEqual([]);
	});
});

// Cell 5.
test.describe('the age of the fetch', () => {
	test.use({ person: { role: 'admin', source: OWN_SOURCE }, locale: 'en-US' });

	test("is worded in English", async ({ page }) => {
		const session = await sessionOf(page);
		const name = await ownSourceName(page, OWN_SOURCE);
		await answersWith(page, name, (view) => ({
			...view,
			fetched_at: new Date(Date.now() - 3 * DAY).toISOString()
		}));

		await page.goto(sourceAddress(name));

		await expect(page.locator('.meta')).toContainText('3 days ago');
		await expect(page.getByRole('link', { name: session.words.sources_back })).toBeVisible();
	});
});

// Cell 9.
test.describe('a member', () => {
	test.use({ person: { role: 'user' } });

	test('meets the for-admins board with no controls and no tab strip', async ({ page }) => {
		const { words } = await sessionOf(page);

		await page.goto(sourceAddress(GONE));

		await expect(page.locator('main h1')).toHaveText(words.for_admins_title);
		await expect(page.getByRole('navigation', { name: words.section_settings })).toHaveCount(0);
		await expect(page.locator('main button')).toHaveCount(0);
	});
});

// Cell 10.
test('a signed-out source address is sent to the sign-in with next set to it', async ({ page }) => {
	const started = startsSignIn(page, NEXT_OF('talks'));

	await page.goto(sourceAddress('talks')).catch(() => undefined);

	await leftForSignIn(page, started);
});
