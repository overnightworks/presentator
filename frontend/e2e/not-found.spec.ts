import type { Page } from '@playwright/test';

import type { SessionView } from '../src/lib/api/session.ts';
import { expect, test } from './person.ts';

async function wordsOf(page: Page): Promise<SessionView['words']> {
	const answer = await page.request.get('/api/session', {
		headers: { Accept: 'application/json' }
	});
	return ((await answer.json()) as SessionView).words;
}

// Cell 1.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`an unknown address at ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'admin' }, colorScheme });

			test('shows the not-found board inside the lobby with its header at 200', async ({
				page
			}, testInfo) => {
				const words = await wordsOf(page);
				await page.setViewportSize({ width, height: 900 });

				const answer = await page.goto('/nope');

				expect(answer?.status()).toBe(200);
				await expect(page.locator('main h1')).toHaveText(words.address_unknown_title);
				await expect(page.locator('main p')).toHaveText(words.address_unknown_explanation);
				await expect(page.locator('header.top .wordmark')).toHaveText(words.wordmark);
				await expect(page.locator('header.top button.account')).toBeVisible();
				const overflow = await page.evaluate(
					() => document.documentElement.scrollWidth - document.documentElement.clientWidth
				);
				expect(overflow).toBeLessThanOrEqual(0);
				await page.screenshot({
					path: testInfo.outputPath(`not-found-${width}-${colorScheme}.png`),
					fullPage: true
				});
			});
		});
	}
}

test.describe('the header section', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 3.
	test('marks Decks as current on / and nothing on an unknown address', async ({ page }) => {
		const words = await wordsOf(page);
		const current = page.locator('header.top nav [aria-current]');

		await page.goto('/');
		await expect(current).toHaveText(words.section_decks);

		await page.goto('/nope');
		await expect(page.locator('main h1')).toBeVisible();
		await expect(current).toHaveCount(0);
	});
});
