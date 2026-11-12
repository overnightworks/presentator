import type { Locator, Page, TestInfo } from '@playwright/test';

import { expect, test } from './person.ts';
import { expectsNoHorizontalScroll, wordsOf } from './scenario.ts';
import {
	ACCESS_TOKEN,
	givenADeployKey,
	holdsRequests,
	opensAnHttpsSource,
	OWN_SOURCE,
	ownSourceName,
	removeButton,
	sourceAddress,
	SOURCES_API,
	type Words
} from './sources.ts';

test.use({ person: { role: 'admin', source: OWN_SOURCE } });

const section = (page: Page, heading: string): Locator =>
	page.locator('section.sec').filter({ has: page.getByRole('heading', { name: heading }) });
const line = (page: Page) => page.getByRole('status');

interface Renewal {
	button: string;
	heading: (words: Words) => string;
	label: (words: Words) => string;
	endpoint: string;
	says: (words: Words) => string;
	opens: (page: Page, words: Words, testInfo: TestInfo) => Promise<string>;
	prepares: (page: Page, words: Words) => Promise<void>;
}

const renewals: Renewal[] = [
	{
		button: 'the deploy key',
		heading: (words) => words['source.accessHeading'],
		label: (words) => words['source.deployKeyRenew'],
		endpoint: 'access',
		says: (words) => words['source.renewedDeployKey'],
		opens: async (page) => {
			const name = await ownSourceName(page, OWN_SOURCE);
			await givenADeployKey(page, name);
			await page.goto(sourceAddress(name));
			return name;
		},
		prepares: async () => undefined
	},
	{
		button: 'the token',
		heading: (words) => words['source.accessHeading'],
		label: (words) => words['source.renew'],
		endpoint: 'access',
		says: (words) => words['source.renewedSecret'],
		opens: async (page, words, testInfo) => (await opensAnHttpsSource(page, words, testInfo)).name,
		prepares: (page, words) =>
			page.getByLabel(words['source.secret'], { exact: true }).fill(ACCESS_TOKEN)
	},
	{
		button: 'the webhook',
		heading: (words) => words['source.webhookHeading'],
		label: (words) => words['source.renew'],
		endpoint: 'webhook-secret',
		says: (words) => words['source.renewedWebhook'],
		opens: async (page, words, testInfo) => (await opensAnHttpsSource(page, words, testInfo)).name,
		prepares: async () => undefined
	}
];

for (const width of [390, 1280]) {
	for (const renewal of renewals) {
		test.describe(`Renew on ${renewal.button} at ${width} px`, () => {
			const press = (page: Page, words: Words) =>
				section(page, renewal.heading(words))
					.getByRole('button', { name: renewal.label(words), exact: true })
					.click();

			test('says it renewed, keeps saying it and clears it when the next press starts', async ({
				page
			}, testInfo) => {
				const words = await wordsOf(page);
				const name = await renewal.opens(page, words, testInfo);
				await page.setViewportSize({ width, height: 900 });
				await expect(line(page)).toHaveCount(0);
				await renewal.prepares(page, words);

				await press(page, words);

				const stated = section(page, renewal.heading(words)).getByRole('status');
				await expect(stated).toHaveText(renewal.says(words));
				await expect(stated).toHaveAttribute('data-state', 'ready');
				await expectsNoHorizontalScroll(page);
				await expect(stated).toBeVisible();
				await expect(page.locator('body')).not.toContainText(ACCESS_TOKEN);

				await renewal.prepares(page, words);
				const held = await holdsRequests(page, `**${SOURCES_API}/${name}/${renewal.endpoint}`);

				await press(page, words);

				await expect(line(page)).toHaveCount(0);
				held.release();
				await expect(stated).toHaveText(renewal.says(words));
			});

			test('says the request failed when it answers 500', async ({ page }, testInfo) => {
				const words = await wordsOf(page);
				const name = await renewal.opens(page, words, testInfo);
				await page.setViewportSize({ width, height: 900 });
				page.on('pageerror', () => undefined);
				await page.route(`**${SOURCES_API}/${name}/${renewal.endpoint}`, (route) =>
					route.fulfill({ status: 500, json: {} })
				);
				await renewal.prepares(page, words);

				await press(page, words);

				const stated = section(page, renewal.heading(words)).getByRole('status');
				await expect(stated).toHaveText(words['source.requestFailed']);
				await expect(stated).toHaveAttribute('data-state', 'failed');
				await expectsNoHorizontalScroll(page);
			});
		});
	}

	test.describe(`Remove and the declined token at ${width} px`, () => {
		test('a declined token shows its banner and no line', async ({ page }, testInfo) => {
			const words = await wordsOf(page);
			await opensAnHttpsSource(page, words, testInfo);
			await page.setViewportSize({ width, height: 900 });
			await page.getByLabel(words['source.secret'], { exact: true }).fill('   ');

			await section(page, words['source.accessHeading'])
				.getByRole('button', { name: words['source.renew'], exact: true })
				.click();

			await expect(page.getByRole('alert')).toHaveText(words['source.refusedSecret']);
			await expect(line(page)).toHaveCount(0);
		});

		test('a count that fails says the request failed beside Remove', async ({ page }, testInfo) => {
			const words = await wordsOf(page);
			const { name } = await opensAnHttpsSource(page, words, testInfo);
			await page.setViewportSize({ width, height: 900 });
			page.on('pageerror', () => undefined);
			await page.route(`**${SOURCES_API}/${name}/removal`, (route) =>
				route.fulfill({ status: 500, json: {} })
			);

			await removeButton(page, words).click();

			const stated = section(page, words['source.removeHeading']).getByRole('status');
			await expect(stated).toHaveText(words['source.requestFailed']);
			await expect(stated).toHaveAttribute('data-state', 'failed');
			await expectsNoHorizontalScroll(page);
		});
	});
}
