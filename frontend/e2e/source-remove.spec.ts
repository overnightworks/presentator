import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import type { Page } from '@playwright/test';

import type { RemovalCounts } from '../src/lib/api/client.ts';
import type { SourceSummary, SourceView, WebhookSecretView } from '../src/lib/api/sources.ts';
import { expect, test } from './person.ts';
import {
	endsTheSession,
	entriesOf,
	expectsHeaderOnOneLine,
	expectsNoHorizontalScroll,
	leftForSignIn,
	SIGN_IN_START,
	startsSignIn,
	tabbedControlWithRing,
	wordsOf
} from './scenario.ts';
import {
	anHttpsAddress,
	cancelOf,
	confirmOf,
	dialogOf,
	opensTheDialog,
	removeButton,
	createsASource,
	JSON_ACCEPT,
	opensAnHttpsSource,
	opensTheForm,
	SAME_ORIGIN,
	SOURCES_API,
	SOURCES_LIST,
	sourceAddress,
	sourceNames,
	tabsOf,
	holdsRequests,
	type Words
} from './sources.ts';

// The wire's own tag of the refused removal, read from the document the types come from.
const wire = JSON.parse(
	readFileSync(join(import.meta.dirname, '../src/lib/api/openapi.json'), 'utf8')
) as {
	paths: Record<
		string,
		{
			delete: {
				responses: Record<
					number,
					{ content: { 'application/problem+json': { schema: { properties: { type: { const: string } } } } } }
				>;
			};
		}
	>;
};
const refusedRemovalType =
	wire.paths['/api/sources/{name}']?.delete.responses[500]?.content['application/problem+json']
		.schema.properties.type.const;

const NEXT_OF = (name: string): string => `%2Fsettings%2Fsources%2F${name}`;
const GONE_ADDRESS = (name: string): string => `${sourceAddress(name)}/remove`;

const previewOf = async (page: Page, name: string): Promise<RemovalCounts> =>
	(await (
		await page.request.get(`${SOURCES_API}/${name}/removal`, { headers: JSON_ACCEPT })
	).json()) as RemovalCounts;

const deletePath = (name: string): string => `**${SOURCES_API}/${name}?*`;

// The notice opens with the catalog's sentence that names the source.
function noticeOf(words: Words, name: string): RegExp {
	return new RegExp(words['source.removedName']({ name }).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
}

const stillTheSameDocument = (page: Page) =>
	page.evaluate(() => Object.assign(window, { stillTheSameDocument: true }));
const isStillTheSameDocument = (page: Page): Promise<boolean> =>
	page.evaluate(() => 'stillTheSameDocument' in window);

// Holds the DELETE of a source on its route until released; answers how many it saw.
const holdsTheDelete = (page: Page, name: string) => holdsRequests(page, deletePath(name));

test.describe('as an admin', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 14.
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`the dialog at ${width} px, ${colorScheme}`, () => {
				test.use({ colorScheme });

				test('shows the name as typed and the preview counts, gives Cancel the focus and saves a screenshot', async ({
					page
				}, testInfo) => {
					const words = await wordsOf(page);
					const { name } = await opensAnHttpsSource(page, words, testInfo);
					await page.setViewportSize({ width, height: 900 });
					await page.route(`**${SOURCES_API}/${name}/removal`, (route) =>
						route.fulfill({ json: { deck_count: 7, run_count: 11 } })
					);

					await opensTheDialog(page, words);

					await expect(dialogOf(page).getByRole('heading', { level: 2 }).locator('.as-typed')).toHaveText(
						name
					);
					await expect(dialogOf(page)).toContainText('7');
					await expect(dialogOf(page)).toContainText('11');
					await expect(cancelOf(page, words)).toBeFocused();
					await expect(confirmOf(page, words)).toBeVisible();
					await expectsNoHorizontalScroll(page);
					if (width === 390) {
						await expectsHeaderOnOneLine(page);
					}
					await page.evaluate(() => document.fonts.ready);
					await page.screenshot({
						path: testInfo.outputPath(`dialog-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}

	// Cell 15.
	test('Cancel and Escape close the dialog and delete nothing, focus returns to Remove and Tab stays inside', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);

		await opensTheDialog(page, words);
		for (let tab = 0; tab < 5; tab++) {
			await page.keyboard.press('Tab');
			// Past its last control a modal hands focus to the browser's own interface, never to the page behind.
			expect(
				await page.evaluate(
					() =>
						document.activeElement === document.body || document.activeElement?.closest('dialog') !== null
				)
			).toBe(true);
		}
		await cancelOf(page, words).click();
		await expect(dialogOf(page)).toBeHidden();
		await expect(removeButton(page, words)).toBeFocused();

		await opensTheDialog(page, words);
		await page.keyboard.press('Escape');
		await expect(dialogOf(page)).toBeHidden();
		await expect(removeButton(page, words)).toBeFocused();
		expect(await sourceNames(page)).toContain(name);
		await page.goto(SOURCES_LIST);
		await expect(page.getByRole('link', { name, exact: true })).toBeVisible();
	});

	test('SR3: Remove sends one DELETE with the counts shown, replaces the page by the list and shows the notice once', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await stillTheSameDocument(page);
		const entries = await entriesOf(page);
		const deletes: string[] = [];
		page.on('request', (sent) => {
			if (sent.method() === 'DELETE') {
				deletes.push(sent.url());
			}
		});
		const shown = await previewOf(page, name);

		await opensTheDialog(page, words);
		await confirmOf(page, words).click();

		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.locator('output.toast')).toHaveText(noticeOf(words, name));
		expect(deletes).toHaveLength(1);
		expect(new URL(deletes[0] as string).searchParams.get('deck_count')).toBe(String(shown.deck_count));
		expect(new URL(deletes[0] as string).searchParams.get('run_count')).toBe(String(shown.run_count));
		expect(await entriesOf(page)).toBe(entries);
		expect(await isStillTheSameDocument(page)).toBe(true);
		await expect(page.getByRole('link', { name, exact: true })).toHaveCount(0);
		expect(await sourceNames(page)).not.toContain(name);
	});

	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`the notice at ${width} px, ${colorScheme}`, () => {
				test.use({ colorScheme });

				test('sits above the empty board of a list that has no source left', async ({
					page
				}, testInfo) => {
					const words = await wordsOf(page);
					await page.setViewportSize({ width, height: 900 });
					const { name } = await opensAnHttpsSource(page, words, testInfo);
					await page.route(`**${SOURCES_API}`, (route) =>
						route.request().method() === 'GET' &&
						new URL(route.request().url()).pathname === SOURCES_API
							? route.fulfill({ json: { sources: [] } })
							: route.continue()
					);

					await opensTheDialog(page, words);
					await confirmOf(page, words).click();

					await expect(page.locator('output.toast')).toHaveText(noticeOf(words, name));
					await expect(page.locator('section.empty')).toBeVisible();
					const toast = await page.locator('output.toast').boundingBox();
					const empty = await page.locator('section.empty').boundingBox();
					expect((toast?.y ?? 0) + (toast?.height ?? 0)).toBeLessThanOrEqual(empty?.y ?? 0);
					await expectsNoHorizontalScroll(page);
					if (width === 390) {
						await expectsHeaderOnOneLine(page);
					}
					await page.screenshot({
						path: testInfo.outputPath(`notice-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}

	// Cell 18.
	test('the notice sentence is found nowhere but the toast', async ({ page }, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const requested: string[] = [];
		const consoled: string[] = [];
		page.on('request', (sent) => requested.push(sent.url()));
		page.on('console', (message) => consoled.push(message.text()));

		await opensTheDialog(page, words);
		await confirmOf(page, words).click();

		const toast = page.locator('output.toast');
		await expect(toast).toHaveText(noticeOf(words, name));
		const sentence = (await toast.innerText()).trim();
		const stored = await page.evaluate(async () => ({
			state: JSON.stringify(history.state),
			url: location.href,
			title: document.title,
			session: JSON.stringify({ ...sessionStorage }),
			local: JSON.stringify({ ...localStorage }),
			cookies: document.cookie,
			databases: JSON.stringify(await indexedDB.databases?.()),
			caches: JSON.stringify(await caches.keys())
		}));
		for (const [where, text] of Object.entries(stored)) {
			expect(text, where).not.toContain(sentence);
		}
		expect(requested.join('\n')).not.toContain(encodeURIComponent(sentence));
		expect(requested.join('\n')).not.toContain(sentence);
		expect(consoled.join('\n')).not.toContain(sentence);
		const body = await page.locator('body').innerText();
		expect(body.split(sentence)).toHaveLength(2);
	});

	// Cell 19.
	test('the toast shows once on its entry, a Fetch now keeps it and every move away drops it', async ({
		page,
		context
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name: gone } = await opensAnHttpsSource(page, words, testInfo);
		const { name: kept } = await opensAnHttpsSource(page, words, testInfo);
		await page.goto(SOURCES_LIST);
		await page.getByRole('link', { name: gone, exact: true }).click();
		await expect(page).toHaveURL(sourceAddress(gone));
		await stillTheSameDocument(page);
		const entries = await entriesOf(page);
		const toast = page.locator('output.toast');

		await opensTheDialog(page, words);
		await confirmOf(page, words).click();
		await expect(toast).toHaveText(noticeOf(words, gone));
		expect(await entriesOf(page)).toBe(entries);

		const reread = page.waitForResponse(
			(received) =>
				new URL(received.url()).pathname === SOURCES_API && received.request().method() === 'GET'
		);
		await page
			.locator('table.sources tbody tr')
			.filter({ has: page.getByRole('link', { name: kept, exact: true }) })
			.getByRole('button', { name: words['source.fetchNow'] })
			.click();
		await reread;
		await expect(toast).toHaveText(noticeOf(words, gone));

		await page.goBack();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.getByRole('link', { name: gone, exact: true })).toHaveCount(0);
		await expect(toast).toHaveCount(0);
		await page.goForward();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(toast).toHaveCount(0);
		expect(await entriesOf(page)).toBe(entries);

		await page.reload();
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['sources.title']);
		await expect(toast).toHaveCount(0);
		const second = await context.newPage();
		await second.goto(SOURCES_LIST);
		await expect(second.getByRole('heading', { level: 1 })).toHaveText(words['sources.title']);
		await expect(second.locator('output.toast')).toHaveCount(0);
		await second.close();
		await tabsOf(page, words).getByRole('link', { name: words['settings.generalTab'] }).click();
		await expect(page).toHaveURL('/settings');
		await tabsOf(page, words).getByRole('link', { name: words['settings.sourcesTab'] }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(toast).toHaveCount(0);
	});

	// Cell 20.
	test("a removed source's address is the not-found board and no history step shows its page", async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await page.goto(SOURCES_LIST);
		await page.getByRole('link', { name, exact: true }).click();
		await expect(page).toHaveURL(sourceAddress(name));
		await opensTheDialog(page, words);
		await confirmOf(page, words).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		const itsPage = page.getByRole('heading', { level: 1, name, exact: true });

		await page.goto(sourceAddress(name));
		await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
		await expect(page.locator('header.top')).toBeVisible();
		await page.goBack();
		await expect(page.locator('main h1')).toHaveText(words['sources.title']);
		await expect(itsPage).toHaveCount(0);
		await page.goBack();
		await expect(page.locator('main h1')).toHaveText(words['sources.title']);
		await expect(itsPage).toHaveCount(0);
		await page.goForward();
		await expect(page.locator('main h1')).toHaveText(words['sources.title']);
		await page.goForward();
		await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
		await expect(itsPage).toHaveCount(0);
	});

	// Cell 21.
	test('a real 409 shows the current counts, deletes nothing and confirms only when asked again', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const before = await previewOf(page, name);
		const textBefore = await dialogOf(page).innerText();

		// A refresh already running on the shared stack drops a fetch and answers
		// without a run, so the fetch repeats until the run count moves.
		await expect
			.poll(async () => {
				await page.request.post(`${SOURCES_API}/${name}/fetch`, { headers: SAME_ORIGIN });
				return (await previewOf(page, name)).run_count;
			})
			.not.toBe(before.run_count);
		await confirmOf(page, words).click();

		await expect(dialogOf(page)).not.toHaveText(textBefore);
		await expect(dialogOf(page)).toBeVisible();
		expect(await sourceNames(page)).toContain(name);
		await expect(page).toHaveURL(sourceAddress(name));
		await expect(confirmOf(page, words)).toBeEnabled();

		await confirmOf(page, words).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cell 22.
	test('a 500 of a refused removal keeps the dialog and its counts, shows the sentence and a retry removes', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const textBefore = await dialogOf(page).locator('p').last().innerText();
		let refusedOnce = false;
		await page.route(deletePath(name), (route) => {
			if (refusedOnce) {
				return route.continue();
			}
			refusedOnce = true;
			return route.fulfill({
				status: 500,
				contentType: 'application/problem+json',
				json: {
					type: refusedRemovalType,
					title: 'Internal Server Error',
					status: 500,
					reason: 'disk-leftover'
				}
			});
		});

		await confirmOf(page, words).click();

		await expect(dialogOf(page).getByRole('alert')).toHaveText(words['source.removeRefused']);
		await expect(dialogOf(page).locator('p').last()).toHaveText(textBefore);
		await expect(confirmOf(page, words)).toBeEnabled();
		await expect(cancelOf(page, words)).toBeEnabled();
		expect(await sourceNames(page)).toContain(name);

		await confirmOf(page, words).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cell 23.
	test('a 500 of any other shape fails loud, keeps the dialog and enables the buttons', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		const failures: Error[] = [];
		page.on('pageerror', (failure) => failures.push(failure));
		await opensTheDialog(page, words);
		await page.route(deletePath(name), (route) => route.fulfill({ status: 500, json: {} }));

		await confirmOf(page, words).click();

		await expect.poll(() => failures.length).toBe(1);
		await expect(dialogOf(page)).toBeVisible();
		await expect(confirmOf(page, words)).toBeEnabled();
		await expect(cancelOf(page, words)).toBeEnabled();
		expect(await sourceNames(page)).toContain(name);
	});

	// The opener counts before the dialog opens; its request is held here.
	for (const width of [390, 1280]) {
		test(`while the removal is counted the opener shows its busy word at ${width} px`, async ({
			page
		}, testInfo) => {
			const words = await wordsOf(page);
			await page.setViewportSize({ width, height: 900 });
			const { name } = await opensAnHttpsSource(page, words, testInfo);
			const held = await holdsRequests(page, `**${SOURCES_API}/${name}/removal`);

			await removeButton(page, words).click();

			const busy = page.getByRole('button', { name: words['source.busyCount'] });
			await expect(busy).toHaveAttribute('aria-busy', 'true');
			await expect(busy).toBeDisabled();
			await expect(busy.locator('.state[data-state="building"]')).toBeVisible();
			for (const button of await page.locator('main button').all()) {
				await expect(button).toBeDisabled();
			}
			await page.screenshot({ path: testInfo.outputPath(`count-busy-${width}.png`) });
			held.release();
			await expect(dialogOf(page)).toBeVisible();
			await expect(removeButton(page, words)).toBeEnabled();
		});
	}

	// Cell 24.
	test('while the DELETE is pending every button is disabled and a double click sends one', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const held = await holdsTheDelete(page, name);

		await confirmOf(page, words).dblclick();

		const busy = dialogOf(page).getByRole('button', { name: words['source.busyRemove'] });
		await expect(busy).toHaveAttribute('aria-busy', 'true');
		await expect(busy).toBeDisabled();
		await expect(busy.locator('.state[data-state="building"]')).toBeVisible();
		await page.screenshot({ path: testInfo.outputPath('remove-busy.png') });
		await expect(cancelOf(page, words)).toBeDisabled();
		await expect(removeButton(page, words)).toBeDisabled();
		for (const button of await page.locator('main button').all()) {
			await expect(button).toBeDisabled();
		}
		held.release();
		await expect(page).toHaveURL(SOURCES_LIST);
		expect(held.seen).toHaveLength(1);
	});

	test('Escape while the DELETE is pending keeps the dialog, and a refused answer is shown in it', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const held = await holdsRequests(page, deletePath(name), {
			answer: (route) =>
				route.fulfill({
					status: 500,
					contentType: 'application/problem+json',
					json: {
						type: refusedRemovalType,
						title: 'Internal Server Error',
						status: 500,
						reason: 'disk-leftover'
					}
				})
		});

		await confirmOf(page, words).click();
		await expect(
			dialogOf(page).getByRole('button', { name: words['source.busyRemove'] })
		).toBeDisabled();
		await page.keyboard.press('Escape');
		await page.keyboard.press('Escape');
		held.release();

		await expect(dialogOf(page).getByRole('alert')).toHaveText(words['source.removeRefused']);
		await expect(confirmOf(page, words)).toBeEnabled();
	});

	// Cell 24b.
	test('a DELETE answered after the person left the page removes the source and changes nothing else', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const held = await holdsTheDelete(page, name);
		await confirmOf(page, words).click();
		await expect.poll(() => held.seen.length).toBe(1);

		// The pending modal makes the page inert, so the link is followed from script.
		await tabsOf(page, words)
			.getByRole('link', { name: words['settings.generalTab'] })
			.evaluate((link: HTMLElement) => link.click());
		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
		const entries = await entriesOf(page);
		const removed = page.waitForResponse(
			(received) => received.url().includes(`${SOURCES_API}/${name}?`) && received.status() === 204
		);
		held.release();
		await removed;

		await expect(page).toHaveURL(/\/settings$/);
		expect(await entriesOf(page)).toBe(entries);
		await tabsOf(page, words).getByRole('link', { name: words['settings.sourcesTab'] }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.locator('output.toast')).toHaveCount(0);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cell 24c.
	test('a DELETE answered while another navigation is still loading changes neither the history nor the notice', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		const held = await holdsTheDelete(page, name);
		await confirmOf(page, words).click();
		await expect.poll(() => held.seen.length).toBe(1);
		const load = await holdsRequests(page, (url) => url.pathname === '/api/settings');
		const entries = await entriesOf(page);

		// The pending modal makes the page inert, so the link is followed from script.
		await tabsOf(page, words)
			.getByRole('link', { name: words['settings.generalTab'] })
			.evaluate((link: HTMLElement) => link.click());
		await expect.poll(() => load.seen.length).toBe(1);
		const removed = page.waitForResponse(
			(received) => received.url().includes(`${SOURCES_API}/${name}?`) && received.status() === 204
		);
		held.release();
		await removed;
		// Two frames let the page act on the answer before the held load goes on.
		await page.evaluate(
			() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))
		);
		load.release();

		await expect(page.getByRole('heading', { level: 1 })).toHaveText(words['settings.title']);
		await expect(page).toHaveURL(/\/settings$/);
		expect(await entriesOf(page)).toBe(entries + 1);
		await tabsOf(page, words).getByRole('link', { name: words['settings.sourcesTab'] }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.locator('output.toast')).toHaveCount(0);
		expect(await sourceNames(page)).not.toContain(name);
	});

	// Cell 24d.
	test('hiding the page closes the removal dialog', async ({ page }, testInfo) => {
		const words = await wordsOf(page);
		await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		await expect(dialogOf(page)).toBeVisible();

		await page.evaluate(() => dispatchEvent(new Event('pagehide')));

		await expect(dialogOf(page)).toBeHidden();
	});

	// Cell 25.
	for (const step of ['the preview', 'the DELETE'] as const) {
		test(`with the session ended, ${step} leaves for the sign-in once with the source address as next`, async ({
			page,
			context
		}, testInfo) => {
			const words = await wordsOf(page);
			const { name } = await opensAnHttpsSource(page, words, testInfo);
			const signIns: string[] = [];
			page.on('request', (sent) => {
				if (new URL(sent.url()).pathname === SIGN_IN_START) {
					signIns.push(new URL(sent.url()).search);
				}
			});
			if (step === 'the DELETE') {
				await opensTheDialog(page, words);
			}
			await endsTheSession(context);
			const started = startsSignIn(page, NEXT_OF(name));

			if (step === 'the preview') {
				await removeButton(page, words).click({ noWaitAfter: true });
			} else {
				await confirmOf(page, words).click({ noWaitAfter: true });
			}

			await leftForSignIn(page, started);
			expect(signIns).toEqual([`?next=${NEXT_OF(name)}`]);
		});
	}

	// Cell 26.
	test('removing one of two sources leaves the other, kills the hook and frees the name and address', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const first = await opensAnHttpsSource(page, words, testInfo);
		const second = await opensAnHttpsSource(page, words, testInfo);
		const hookUrl = ((await (
			await page.request.get(`${SOURCES_API}/${first.name}`, { headers: JSON_ACCEPT })
		).json()) as SourceView).hook_url;
		const renewed = (await (
			await page.request.post(`${SOURCES_API}/${first.name}/webhook-secret`, { headers: SAME_ORIGIN })
		).json()) as WebhookSecretView;
		const hook = (): Promise<number> =>
			page.request
				.post(hookUrl, { headers: { Authorization: `Bearer ${renewed.webhook_secret}` } })
				.then((answer) => answer.status());
		await page.goto(sourceAddress(first.name));
		const kept = await (
			await page.request.get(SOURCES_API, { headers: JSON_ACCEPT })
		).json() as { sources: SourceSummary[] };
		const stateBefore = kept.sources.find((source) => source.name === second.name)?.state;
		expect(await hook()).not.toBe(404);

		await opensTheDialog(page, words);
		await confirmOf(page, words).click();

		await expect(page).toHaveURL(SOURCES_LIST);
		await expect(page.getByRole('link', { name: second.name, exact: true })).toBeVisible();
		const after = await (
			await page.request.get(SOURCES_API, { headers: JSON_ACCEPT })
		).json() as { sources: SourceSummary[] };
		expect(after.sources.find((source) => source.name === second.name)?.state).toBe(stateBefore);
		expect(await hook()).toBe(404);
		await opensTheForm(page);
		await createsASource(page, words, { name: first.name, url: anHttpsAddress(first.name) });
		expect(await sourceNames(page)).toContain(first.name);
	});

	// Cell 27.
	test('the old removal address is the not-found board for a GET and a 404 for a POST, and the source stays', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		const { name } = await opensAnHttpsSource(page, words, testInfo);

		await page.goto(GONE_ADDRESS(name));
		await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
		const posted = await page.request.post(GONE_ADDRESS(name), { headers: SAME_ORIGIN });

		expect(posted.status()).toBe(404);
		expect(await sourceNames(page)).toContain(name);
	});

	// Cell 29.
	test("the source page's back link and Settings tab are client navigations", async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		await opensAnHttpsSource(page, words, testInfo);
		await stillTheSameDocument(page);
		const documents: string[] = [];
		page.on('request', (sent) => {
			if (sent.resourceType() === 'document') {
				documents.push(sent.url());
			}
		});

		await page.getByRole('link', { name: words['source.back'] }).click();
		await expect(page).toHaveURL(SOURCES_LIST);
		await page.goBack();
		await tabsOf(page, words).getByRole('link', { name: words['settings.generalTab'] }).click();
		await expect(page).toHaveURL('/settings');

		expect(documents).toEqual([]);
		expect(await isStillTheSameDocument(page)).toBe(true);
	});

	// Cell 30.
	test('the dialog shows a focus ring on Cancel and Remove at 390 px, in order', async ({
		page
	}, testInfo) => {
		const words = await wordsOf(page);
		await page.setViewportSize({ width: 390, height: 900 });
		await opensAnHttpsSource(page, words, testInfo);
		await opensTheDialog(page, words);
		await cancelOf(page, words).focus();

		const focused = [words['source.removeCancel']];
		focused.push((await tabbedControlWithRing(page)) as string);

		expect(focused).toEqual([words['source.removeCancel'], words['source.removeConfirm']]);
	});
});
