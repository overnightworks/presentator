import type { Page } from '@playwright/test';

import type { ConnectionCheckView } from '../src/lib/api/sources.ts';
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
	aName,
	anHttpsAddress,
	anSshAddress,
	type AccessKind,
	checks,
	choosesAccess,
	CHECK_API,
	createsASource,
	fillsTheForm,
	JSON_ACCEPT,
	KEY_DRAFT_API,
	keyDraftOf,
	NEW_ADDRESS,
	opensTheForm,
	SAME_ORIGIN,
	secretField,
	shellAt,
	sourceNames,
	SOURCES_API,
	SOURCES_LIST,
	tabsOf,
	type Words
} from './sources.ts';

const NEXT = '%2Fsettings%2Fsources%2Fnew';

// The source's own page: the dots stand for its secrets and the Add form is gone.
const showsTheSourcePage = async (page: Page, words: Words): Promise<void> => {
	await expect(page.locator('body')).toContainText(words.source_secret_dots);
	await expect(page.locator('input[name="name"]')).toHaveCount(0);
};

const showsTheEmptyForm = async (page: Page): Promise<void> => {
	await expect(page.locator('main form')).toBeVisible();
	await expect(page.locator('input[name="name"]')).toHaveValue('');
};

// Playwright turns Chromium's back/forward cache off; this spec turns it on, so
// a page left by a document navigation is restored the way a person's is.
test.use({ launchOptions: { ignoreDefaultArgs: ['--disable-back-forward-cache'] } });

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 1.
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`the empty Add source form at ${width} px, ${colorScheme}`, () => {
				test.use({ colorScheme });

				test('shows the fields, the three segments, the key and the tab strip, and saves a screenshot', async ({
					page
				}, testInfo) => {
					const { words } = await sessionOf(page);
					await page.setViewportSize({ width, height: 900 });

					const shell = await page.goto(NEW_ADDRESS);

					expect(shell?.status()).toBe(200);
					const draft = await keyDraftOf(page);
					await expect(page).toHaveTitle(words.sources_add);
					await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.sources_add);
					const tabs = tabsOf(page, words);
					await expect(tabs.getByRole('link', { name: words.settings_sources })).toHaveAttribute(
						'aria-current',
						'page'
					);
					await expect(tabs.getByRole('link', { name: words.settings_title })).not.toHaveAttribute(
						'aria-current'
					);
					await expect(page.getByLabel(words.source_name, { exact: true })).toHaveValue('');
					await expect(page.getByLabel(words.source_url, { exact: true })).toHaveValue('');
					await expect(page.getByRole('group', { name: words.source_access })).toBeVisible();
					await expect(page.getByRole('radio')).toHaveCount(3);
					await expect(page.locator('.seg label')).toHaveText([
						words.source_access_ssh,
						words.source_access_https,
						words.source_access_file
					]);
					await expect(page.getByRole('radio', { name: words.source_access_https })).toBeChecked();
					await expect(secretField(page, words)).toHaveAttribute('type', 'password');
					await expect(secretField(page, words)).toHaveValue('');
					await expect(page.getByText(words.source_deploy_key_heading)).toBeVisible();
					await expect(page.getByText(words.source_deploy_key_hint)).toBeVisible();
					await expect(page.getByText(draft.public_key, { exact: true })).toBeVisible();
					await expect(page.getByRole('button', { name: words.source_check })).toBeEnabled();
					await expect(page.getByRole('button', { name: words.source_create })).toBeDisabled();
					await expectsNoHorizontalScroll(page);
					if (width === 390) {
						await expectsHeaderOnOneLine(page);
					}
					await page.getByRole('button', { name: words.source_secret_info }).click();
					await expect(page.locator('#secret-hint')).toBeVisible();
					await page.keyboard.press('Escape');
					await page.evaluate(() => document.fonts.ready);
					await page.screenshot({
						path: testInfo.outputPath(`source-new-form-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}

	// Cells 1 and 14.
	test('the list opens the form and the back link returns to the list, both as documents', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await page.goto(SOURCES_LIST);

		await page.getByRole('link', { name: words.sources_add }).first().click();
		await expect(page).toHaveURL(NEW_ADDRESS);
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.sources_add);
		const back = page.getByRole('link', { name: words.sources_back });
		await expect(back).toHaveAttribute('href', SOURCES_LIST);
		const listed = page.waitForResponse(
			(received) => new URL(received.url()).pathname === SOURCES_LIST
		);
		await back.click();

		expect((await listed).status()).toBe(200);
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.locator('script[src*="/_app/"]')).toHaveCount(0);
	});

	// Cell 2.
	test('a reachable check shows the commit it read and leaves Create enabled when the access changes', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await fillsTheForm(page, words, { name, url: anHttpsAddress(name) });

		const found = await checks(page, words);

		const banner = page.getByRole('status').filter({ has: page.locator('.state') });
		await expect(banner).toHaveText(
			words.source_check_reachable.replace('{commit}', found.commit ?? '')
		);
		await expect(banner).toHaveAttribute('data-banner', 'ok');
		await expect(page.getByRole('button', { name: words.source_create })).toBeEnabled();
		await choosesAccess(page, words.source_access_ssh);
		await expect(page.getByRole('button', { name: words.source_create })).toBeEnabled();
	});

	const markup = '<b onclick="alert(1)">not found</b> & gone';
	const failures: {
		failure: NonNullable<ConnectionCheckView['failure']>;
		sentence: (words: Words) => string;
		detail: string | null;
	}[] = [
		{ failure: 'refused', sentence: (words) => words.source_run_refused, detail: null },
		{ failure: 'unreachable', sentence: (words) => words.source_run_unreachable, detail: null },
		{ failure: 'failed', sentence: (words) => words.source_run_failed, detail: markup },
		{
			failure: 'credential-unresolvable',
			sentence: (words) => words.source_run_secret,
			detail: null
		}
	];
	for (const { failure, sentence, detail } of failures) {
		test(`a ${failure} check shows its reason, git's line as text and leaves Create disabled`, async ({
			page
		}, testInfo) => {
			const { words } = await sessionOf(page);
			const name = aName(testInfo);
			await opensTheForm(page);
			await page.route(`**${CHECK_API}`, (route) =>
				route.fulfill({
					json: { commit: null, detail, failure, fingerprint: null } satisfies ConnectionCheckView
				})
			);
			await fillsTheForm(page, words, { name, url: anHttpsAddress(name) });

			await checks(page, words);

			const banner = page.locator('.banner[data-banner="fail"]');
			await expect(banner).toContainText(sentence(words));
			if (detail === null) {
				await expect(banner.locator('samp')).toHaveCount(0);
			} else {
				await expect(banner.locator('samp')).toHaveText(detail);
				await expect(banner.locator('samp b')).toHaveCount(0);
			}
			await expect(page.getByRole('button', { name: words.source_create })).toBeDisabled();
		});
	}

	test('an address with a user part is refused by the real check with the access sentence', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await fillsTheForm(page, words, {
			name,
			url: `https://user:token@git.example.invalid/${name}.git`
		});

		await checks(page, words).catch(() => undefined);

		await expect(page.getByRole('alert')).toHaveText(words.source_refused_access);
		await expect(page.getByRole('button', { name: words.source_create })).toBeDisabled();
	});

	// The harness's stores cannot consume a draft key, so the refusal and the
	// newer key come from fulfilled answers; the refusal's body is a real one
	// with its reason changed.
	test('a create refused for its key shows its sentence and the key read again', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		const newerKey = { id: 'a-newer-draft', public_key: 'ssh-ed25519 AAAAnewer a-newer-key' };
		let createRefused = false;
		await page.route(`**${KEY_DRAFT_API}`, (route) =>
			createRefused ? route.fulfill({ json: newerKey }) : route.continue()
		);
		const real = await page.request.post(SOURCES_API, {
			headers: SAME_ORIGIN,
			data: {
				name: 'Not A Name',
				url: anHttpsAddress(name),
				access: 'https-token',
				secret: 's',
				fingerprint: 'f',
				key_draft_id: null
			}
		});
		expect(real.status()).toBe(422);
		const refusal = (await real.json()) as { reason: string };
		await page.route(`**${SOURCES_API}`, (route) => {
			if (route.request().method() !== 'POST') {
				return route.continue();
			}
			createRefused = true;
			return route.fulfill({
				status: 422,
				contentType: 'application/problem+json',
				json: { ...refusal, reason: 'deploy-key-unavailable' }
			});
		});
		await opensTheForm(page);
		const before = await keyDraftOf(page);
		await expect(page.getByText(before.public_key, { exact: true })).toBeVisible();
		await choosesAccess(page, words.source_access_ssh);
		await fillsTheForm(page, words, { name, url: anSshAddress(name), secret: '' });
		await checks(page, words);

		await page.getByRole('button', { name: words.source_create }).click();

		await expect(page.getByRole('alert')).toHaveText(words.source_refused_deploy_key);
		await expect(page.getByText(newerKey.public_key, { exact: true })).toBeVisible();
		await expect(page.getByText(before.public_key, { exact: true })).toHaveCount(0);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cells 3 and 4: the created-once view.
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`the created-once view at ${width} px, ${colorScheme}`, () => {
				test.use({ colorScheme });

				test('shows the webhook once in the same document, and nothing keeps its secret', async ({
					page,
					context
				}, testInfo) => {
					const { words } = await sessionOf(page);
					await page.setViewportSize({ width, height: 900 });
					const consoleLines: string[] = [];
					const requested: string[] = [];
					page.on('console', (message) => consoleLines.push(message.text()));
					page.on('request', (sent) => requested.push(sent.url()));
					const name = aName(testInfo);
					await opensTheForm(page);
					const entries = await entriesOf(page);
					await page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));

					const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });

					await expect(page.getByRole('heading', { level: 1 })).toHaveText(
						`${name} ${words.source_created}`
					);
					await expect(
						page.getByRole('group', { name: words.source_webhook_address })
					).toContainText(created.hook_url);
					const secretRow = page.getByRole('group', {
						name: new RegExp(words.source_webhook_secret)
					});
					await expect(secretRow).toContainText(created.webhook_secret);
					await expect(page.locator('.once')).toHaveText(words.source_webhook_once);
					await expect(
						page.getByRole('status').filter({ hasText: words.source_created_toast })
					).toBeVisible();
					await expect(page.locator('main form')).toHaveCount(0);
					await expect(page.locator('body')).not.toContainText(ACCESS_TOKEN);
					await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
					expect(await entriesOf(page)).toBe(entries);
					expect(await page.evaluate(() => 'stillTheSameDocument' in window)).toBe(true);
					await expectsNoHorizontalScroll(page);
					await page.evaluate(() => document.fonts.ready);
					await page.screenshot({
						path: testInfo.outputPath(`source-created-${width}-${colorScheme}.png`),
						fullPage: true
					});

					const called = await page.request.post(created.hook_url, {
						headers: { Authorization: `Bearer ${created.webhook_secret}` }
					});
					expect(called.status()).toBe(204);
					const wire = await page.request.get(`${SOURCES_API}/${name}`, { headers: JSON_ACCEPT });
					expect(wire.status()).toBe(200);
					expect(await wire.text()).not.toContain(created.webhook_secret);
					const shell = await shellAt(page, `${SOURCES_LIST}/${name}`);
					expect(shell.status()).toBe(200);
					expect(await shell.text()).not.toContain(created.webhook_secret);

					// Cell 4: nothing keeps the secret.
					const kept = await page.evaluate(() => ({
						session: JSON.stringify({ ...window.sessionStorage }),
						local: JSON.stringify({ ...window.localStorage }),
						cookie: document.cookie,
						title: document.title,
						state: JSON.stringify(history.state),
						address: location.href
					}));
					expect(Object.values(kept).join('\n')).not.toContain(created.webhook_secret);
					expect(JSON.stringify(await context.cookies())).not.toContain(created.webhook_secret);
					expect(requested.join('\n')).not.toContain(created.webhook_secret);
					expect(consoleLines.join('\n')).not.toContain(created.webhook_secret);
					await page.reload();
					await showsTheSourcePage(page, words);
					await expect(page.locator('body')).not.toContainText(created.webhook_secret);
				});
			});
		}
	}

	// Cells 29 and 30.
	test('the General tab then Back shows the source page with dots, never the secret or the form', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });

		await tabsOf(page, words).getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL('/settings');
		await page.goBack();
		await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
		await showsTheSourcePage(page, words);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);

		await page.goForward();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.settings_title);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
		await page.goBack();
		await showsTheSourcePage(page, words);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
	});

	// Cell 30b.
	test('a reload at the source address, the General tab, then Back shows the source page with dots', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });
		await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
		await page.reload();
		await showsTheSourcePage(page, words);

		await tabsOf(page, words).getByRole('link', { name: words.settings_title }).click();
		await expect(page).toHaveURL('/settings');
		await page.goBack();

		await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
		await showsTheSourcePage(page, words);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
	});

	// Cell 31.
	test('Done takes the address the bar shows as a replace, and back and forward never show the secret', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await page.goto(SOURCES_LIST);
		await opensTheForm(page);
		const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });
		const entries = await entriesOf(page);

		const left = page.waitForResponse(
			(received) => new URL(received.url()).pathname === `${SOURCES_LIST}/${name}`
		);
		await page.getByRole('link', { name: words.source_created_done }).click();

		expect((await left).status()).toBe(200);
		await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
		await showsTheSourcePage(page, words);
		expect(await entriesOf(page)).toBe(entries);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
		await page.goBack();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
		await page.goForward();
		await expect(page).toHaveURL(`${SOURCES_LIST}/${name}`);
		await showsTheSourcePage(page, words);
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
		await page.goBack();
		await expect(page).toHaveURL(SOURCES_LIST);
	});

	// Cell 5: a page hidden on its created view is shown as the empty form.
	test('hiding the page on the created view leaves the empty form without the secret', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });
		await expect(page.locator('.once')).toBeVisible();

		await page.evaluate(() => dispatchEvent(new Event('pagehide')));

		await expect(page.locator('main form')).toBeVisible();
		await expect(secretField(page, words)).toHaveValue('');
		await expect(page.locator('body')).not.toContainText(created.webhook_secret);
	});

	// Cell 5: a page hidden while a secret is typed shows the empty form.
	test('hiding the page on the form forgets the typed fields and the secret', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const name = aName(testInfo);
		await opensTheForm(page);
		await fillsTheForm(page, words, {
			name,
			url: anHttpsAddress(name),
			secret: 'a-typed-access-token'
		});

		await page.evaluate(() => dispatchEvent(new Event('pagehide')));

		await showsTheEmptyForm(page);
		await expect(secretField(page, words)).toHaveValue('');
	});

	// Cell 5, the back-forward cache, which this spec enables in Chromium.
	test.describe('the back/forward cache', () => {
		const restoredFromTheCache = (page: Page): Promise<boolean> =>
			page.evaluate(() => 'restoredFromTheCache' in window);

		// Chromium keeps a page out of its cache when its response says no-store
		// and the page made a network request of its own; the lobby says no-store
		// on every answer. The control finds that out, and the cell is skipped
		// with the browser's own reasons instead of passing without a restore.
		const whyNotRestored = (page: Page): Promise<string> =>
			page.evaluate(() => {
				const entry = performance.getEntriesByType(
					'navigation'
				)[0] as PerformanceNavigationTiming & {
					notRestoredReasons?: { reasons: { reason: string }[] };
				};
				return (entry.notRestoredReasons?.reasons ?? []).map((each) => each.reason).join(', ');
			});

		test('restores the Add page under the source address as the empty form once the control page is restored', async ({
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

			await opensTheForm(page);
			await page.goto(SOURCES_LIST);
			await page.goBack();
			await expect(page.getByRole('heading', { level: 1 })).toHaveText(words.sources_add);
			const restored = await restoredFromTheCache(page);
			test.skip(
				!restored,
				`the control page is not restored from the cache: ${await whyNotRestored(page)}`
			);

			const name = aName(testInfo);
			await page.reload();
			expect(await restoredFromTheCache(page)).toBe(false);
			const created = await createsASource(page, words, { name, url: anHttpsAddress(name) });
			await page.getByRole('link', { name: words.sources_back }).click();
			await expect(page).toHaveURL(SOURCES_LIST);
			await page.goBack();

			expect(await restoredFromTheCache(page), 'the created view was restored from the cache').toBe(
				true
			);
			await showsTheEmptyForm(page);
			await expect(page.locator('body')).not.toContainText(created.webhook_secret);
		});
	});

	// Cell 6.
	test('a real refusal names its reason, keeps the fields and makes no source', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const taken = aName(testInfo, 'taken');
		const other = aName(testInfo, 'other');
		const takenAddress = anHttpsAddress(taken);
		await opensTheForm(page);
		await createsASource(page, words, { name: taken, url: takenAddress });
		await opensTheForm(page);
		const refusal = page.getByRole('alert');
		const name = page.getByLabel(words.source_name, { exact: true });
		const url = page.getByLabel(words.source_url, { exact: true });

		await fillsTheForm(page, words, { name: taken, url: anHttpsAddress(other) });
		await checks(page, words);
		await page.getByRole('button', { name: words.source_create }).click();
		await expect(refusal).toHaveText(words.source_refused_duplicate_name);
		await expect(name).toHaveValue(taken);
		await expect(secretField(page, words)).toHaveValue(ACCESS_TOKEN);

		await name.fill(other);
		await url.fill(takenAddress);
		await checks(page, words);
		await page.getByRole('button', { name: words.source_create }).click();
		await expect(refusal).toHaveText(words.source_refused_duplicate_url);
		await expect(url).toHaveValue(takenAddress);

		await name.fill('new');
		await url.fill(anHttpsAddress(other));
		await checks(page, words);
		await page.getByRole('button', { name: words.source_create }).click();
		await expect(refusal).toHaveText(words.source_refused_name);
		await expect(name).toHaveValue('new');

		await name.fill(other);
		await secretField(page, words).fill('');
		await page.getByRole('button', { name: words.source_create }).click();
		await expect(refusal).toHaveText(words.source_refused_secret);

		const names = await sourceNames(page);
		expect(names).toContain(taken);
		expect(names).not.toContain(other);
		expect(names).not.toContain('new');
	});

	// Cell 7.
	test('a failed write shows no created view, enables Create again and fails loudly', async ({
		page
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const failures: Error[] = [];
		page.on('pageerror', (failure) => failures.push(failure));
		const name = aName(testInfo);
		await opensTheForm(page);
		await page.route(`**${SOURCES_API}`, (route) =>
			route.request().method() === 'POST'
				? route.fulfill({ status: 500, json: {} })
				: route.continue()
		);
		await fillsTheForm(page, words, { name, url: anHttpsAddress(name) });
		await checks(page, words);

		await page.getByRole('button', { name: words.source_create }).click();

		await expect.poll(() => failures.length).toBe(1);
		await expect(page.getByRole('button', { name: words.source_create })).toBeEnabled();
		await expect(page.getByRole('button', { name: words.source_check })).toBeEnabled();
		await expect(page.getByLabel(words.source_name, { exact: true })).toHaveValue(name);
		await expect(secretField(page, words)).toHaveValue(ACCESS_TOKEN);
		await expect(page.locator('.once')).toHaveCount(0);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cell 10.
	test('an ended session leaves for the sign-in once from Check, with this address as next', async ({
		page,
		context
	}, testInfo) => {
		const { words } = await sessionOf(page);
		const signInRequests: string[] = [];
		page.on('request', (sent) => {
			if (new URL(sent.url()).pathname === SIGN_IN_START) {
				signInRequests.push(new URL(sent.url()).search);
			}
		});
		const name = aName(testInfo);
		await opensTheForm(page);
		await fillsTheForm(page, words, { name, url: anHttpsAddress(name) });
		await endsTheSession(context);
		const started = startsSignIn(page, NEXT);

		await page.getByRole('button', { name: words.source_check }).click();

		await leftForSignIn(page, started);
		expect(signInRequests).toEqual([`?next=${NEXT}`]);
	});

	// Cell 11.
	test('the segments are reachable and keyboard-operable, On this box included', async ({
		page
	}) => {
		const { words } = await sessionOf(page);
		await opensTheForm(page);

		await page.getByText(words.source_access_file, { exact: true }).click();
		await expect(page.getByRole('radio', { name: words.source_access_file })).toBeChecked();

		await page.getByRole('radio', { name: words.source_access_file }).focus();
		await page.keyboard.press('ArrowLeft');
		await expect(page.getByRole('radio', { name: words.source_access_https })).toBeChecked();
		await page.keyboard.press('ArrowLeft');
		await expect(page.getByRole('radio', { name: words.source_access_ssh })).toBeChecked();
	});

	// Cell 12.
	test.describe('at 390 px', () => {
		test('every control shows a focus ring, in the order of the page', async ({ page }) => {
			const { words } = await sessionOf(page);
			await page.setViewportSize({ width: 390, height: 900 });
			await opensTheForm(page);
			await page.locator('body').click({ position: { x: 1, y: 1 } });

			const focused: (string | null)[] = [];
			for (let tab = 0; tab < 14; tab++) {
				focused.push(await tabbedControlWithRing(page));
			}

			// The info button has no text, so it is named by its class.
			const controlsInOrder = [
				words.sources_back,
				words.source_name,
				words.source_url,
				words.source_access_https,
				'info',
				words.source_secret,
				words.source_copy,
				words.source_check
			];
			const positions = controlsInOrder.map((control) => focused.indexOf(control));
			expect(
				positions.every((position) => position >= 0),
				focused.join(' | ')
			).toBe(true);
			expect(positions).toEqual([...positions].sort((a, b) => a - b));
		});
	});

	// Cell 13.
	test('Copy puts the shown value on the clipboard in Chromium and raises no error in Firefox', async ({
		page,
		context,
		browserName
	}) => {
		const { words } = await sessionOf(page);
		const failures: Error[] = [];
		page.on('pageerror', (failure) => failures.push(failure));
		if (browserName === 'chromium') {
			await context.grantPermissions(['clipboard-read', 'clipboard-write']);
		}
		await opensTheForm(page);
		const draft = await keyDraftOf(page);

		await page.getByRole('button', { name: words.source_copy }).click();

		if (browserName === 'chromium') {
			await expect
				.poll(() => page.evaluate(() => navigator.clipboard.readText()))
				.toBe(draft.public_key);
		}
		expect(failures).toEqual([]);
	});

	// Cell 11 (wire): the radio values are the generated ones.
	test('each access segment carries the generated access kind as its value', async ({ page }) => {
		const { words } = await sessionOf(page);
		const kinds: Record<string, AccessKind> = {
			[words.source_access_ssh]: 'ssh-deploy-key',
			[words.source_access_https]: 'https-token',
			[words.source_access_file]: 'local-folder'
		};
		await opensTheForm(page);
		for (const [label, kind] of Object.entries(kinds)) {
			await expect(page.getByRole('radio', { name: label })).toHaveAttribute('value', kind);
		}
	});
});

// Cell 8.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`a member at Add source, ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'user' }, colorScheme });

			test('meets the for-admins board inside the lobby over a 200 shell', async ({
				page
			}, testInfo) => {
				const { words } = await sessionOf(page);
				await page.setViewportSize({ width, height: 900 });

				const shell = await page.goto(NEW_ADDRESS);

				expect(shell?.status()).toBe(200);
				expect((await page.request.post(KEY_DRAFT_API, { headers: SAME_ORIGIN })).status()).toBe(
					403
				);
				await expect(page.locator('main h1')).toHaveText(words.for_admins_title);
				await expect(page.locator('main p')).toHaveText(words.for_admins_explanation);
				await expect(page.getByRole('navigation', { name: words.section_settings })).toHaveCount(0);
				await expect(page.getByRole('link', { name: words.section_settings })).toHaveCount(0);
				await expectsNoHorizontalScroll(page);
				await page.screenshot({
					path: testInfo.outputPath(`source-new-for-admins-${width}-${colorScheme}.png`),
					fullPage: true
				});
			});
		});
	}
}

// Cell 9.
test('a signed-out Add source is sent to the sign-in with next set to its address', async ({
	page
}) => {
	const started = startsSignIn(page, NEXT);

	await page.goto(NEW_ADDRESS).catch(() => undefined);

	await leftForSignIn(page, started);
});
