import type { Page, Route } from '@playwright/test';

import type { SourceFetchView } from '../src/lib/api/sources.ts';
import { expect, test } from './person.ts';
import { expectsNoHorizontalScroll, wordsOf } from './scenario.ts';
import {
	aLocalAddress,
	aName,
	createsASource,
	holdsRequests,
	opensAnHttpsSource,
	opensTheForm,
	sourceAddress,
	SOURCES_API,
	SOURCES_LIST,
	type Words
} from './sources.ts';

const run = (overrides: Partial<NonNullable<SourceFetchView['run']>>) => ({
	at: '2026-11-09T10:00:00Z',
	commit: 'abc1234',
	outcome: 'success' as const,
	reason: null,
	state: 'reachable' as const,
	...overrides
});
const answer = (overrides: Partial<SourceFetchView>): SourceFetchView => ({
	run: run({}),
	decks_built: 0,
	builds_failed: 0,
	decks_removed: 0,
	...overrides
});

interface Row {
	ended: string;
	answered: () => (route: Route) => Promise<void>;
	says: (words: Words) => string;
	shape: 'ready' | 'never-built' | 'failed';
}
const rows: Row[] = [
	{
		ended: 'a built deck',
		answered: () => (route) => route.fulfill({ json: answer({ decks_built: 1 }) }),
		says: (words) => `${words['source.fetchChanged']} ${words['source.fetchBuilt']({ count: 1 })}`,
		shape: 'ready'
	},
	{
		ended: 'a built deck and a failed build',
		answered: () => (route) =>
			route.fulfill({ json: answer({ decks_built: 1, builds_failed: 1 }) }),
		says: (words) =>
			`${words['source.fetchChanged']} ${words['source.fetchBuilt']({ count: 1 })}, ${words['source.fetchFailed']({ count: 1 })}`,
		shape: 'failed'
	},
	{
		ended: 'a removed deck',
		answered: () => (route) => route.fulfill({ json: answer({ decks_removed: 1 }) }),
		says: (words) => `${words['source.fetchChanged']} ${words['source.fetchRemoved']({ count: 1 })}`,
		shape: 'ready'
	},
	{
		ended: 'nothing new',
		answered: () => (route) => route.fulfill({ json: answer({}) }),
		says: (words) => words['source.fetchNothingNew'],
		shape: 'ready'
	},
	{
		ended: 'an unreadable source',
		answered: () => (route) =>
			route.fulfill({
				json: answer({
					run: run({ outcome: 'failure', commit: null, reason: 'refused', state: 'refused' })
				})
			}),
		says: (words) => words['source.runRefused'],
		shape: 'failed'
	},
	{
		ended: 'the lock busy',
		answered: () => (route) => route.fulfill({ json: answer({ run: null }) }),
		says: (words) => words['source.fetchBusy'],
		shape: 'never-built'
	},
	{
		ended: 'a request that failed',
		answered: () => (route) => route.fulfill({ status: 500, json: {} }),
		says: (words) => words['source.requestFailed'],
		shape: 'failed'
	}
];

test.use({ person: { role: 'admin' } });

const line = (page: Page) => page.getByRole('status');

for (const surface of ['the source page', 'the list'] as const) {
	const opens = async (page: Page, name: string): Promise<void> => {
		await page.goto(surface === 'the list' ? SOURCES_LIST : sourceAddress(name));
	};
	const scope = (page: Page, name: string) =>
		surface === 'the list' ? page.locator('tr', { hasText: name }) : page.locator('.actions');
	const fetchButton = (page: Page, words: Words, name: string) =>
		scope(page, name).getByRole('button', { name: words['source.fetchNow'] });

	test.describe(`Fetch now on ${surface}`, () => {
		for (const width of [390, 1280]) {
			for (const row of rows) {
				test(`says ${row.ended} at ${width} px and keeps saying it through the reread`, async ({
					page
				}, testInfo) => {
					const words = await wordsOf(page);
					const { name } = await opensAnHttpsSource(page, words, testInfo);
					await page.setViewportSize({ width, height: 900 });
					await opens(page, name);
					page.on('pageerror', () => undefined);
					await page.route(`**${SOURCES_API}/${name}/fetch`, row.answered());
					await expect(line(page)).toHaveCount(0);

					await fetchButton(page, words, name).click();

					const stated = scope(page, name).getByRole('status');
					await expect(stated).toHaveText(row.says(words));
					await expect(stated).toHaveAttribute('data-state', row.shape);
					await expect(fetchButton(page, words, name)).toBeEnabled();
					await expectsNoHorizontalScroll(page);
					await expect(stated).toBeVisible();
					await expect(stated).toHaveText(row.says(words));
				});
			}
		}

		test('says nothing new after a real fetch of a folder source', async ({ page }, testInfo) => {
			const words = await wordsOf(page);
			const name = aName(testInfo);
			await opensTheForm(page);
			await createsASource(page, words, { name, url: await aLocalAddress(page, name) });
			await opens(page, name);

			await fetchButton(page, words, name).click();

			const stated = scope(page, name).getByRole('status');
			await expect(stated).toHaveText(words['source.fetchNothingNew']);
			await expect(stated).toHaveAttribute('data-state', 'ready');
		});

		test('clears the line when the next press starts and never shows it before the request ends', async ({
			page
		}, testInfo) => {
			const words = await wordsOf(page);
			const { name } = await opensAnHttpsSource(page, words, testInfo);
			await opens(page, name);
			await page.route(`**${SOURCES_API}/${name}/fetch`, (route) =>
				route.fulfill({ json: answer({ decks_built: 1 }) })
			);
			await fetchButton(page, words, name).click();
			await expect(line(page)).toHaveCount(1);
			await page.unroute(`**${SOURCES_API}/${name}/fetch`);
			const held = await holdsRequests(page, `**${SOURCES_API}/${name}/fetch`);

			await fetchButton(page, words, name).click();

			await expect(line(page)).toHaveCount(0);
			held.release();
			await expect(line(page)).toHaveCount(1);
		});
	});
}
