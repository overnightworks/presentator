import { expect, test } from './person.ts';
import { leftForSignIn, startsSignIn, wordsOf } from './scenario.ts';

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`an unknown address at ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'admin' }, colorScheme });

			// LB4.
			test('shows the not-found board inside the lobby with its header at 200', async ({
				page
			}, testInfo) => {
				const words = await wordsOf(page);
				await page.setViewportSize({ width, height: 900 });

				const answer = await page.goto('/nope');

				expect(answer?.status()).toBe(200);
				await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
				await expect(page.locator('main p')).toHaveText(words['error.notFoundExplanation']);
				await expect(page.locator('main .note-card a.btn.primary')).toHaveText(words['deck.decksLink']);
				await expect(page.locator('main .note-card a.btn.primary')).toHaveAttribute('href', '/');
				await expect(page.locator('header.top .wordmark')).toHaveText(words['app.name']);
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

// The address of the page this lobby once had for a person's own choices.
const ACCOUNT = '/account';

for (const role of ['admin', 'user'] as const) {
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`/account of ${role === 'admin' ? 'an admin' : 'a member'} at ${width} px, ${colorScheme}`, () => {
				test.use({ person: { role }, colorScheme });

				// LB2.
				test('is the not-found board with the header and no form', async ({ page }, testInfo) => {
					const words = await wordsOf(page);
					await page.setViewportSize({ width, height: 900 });

					const answer = await page.goto(ACCOUNT);

					expect(answer?.status()).toBe(200);
					await expect(page.locator('main h1')).toHaveText(words['error.notFoundTitle']);
					await expect(page.locator('header.top .wordmark')).toHaveText(words['app.name']);
					await expect(page.locator('main select, main form')).toHaveCount(0);
					await expect(page.getByRole('link', { name: words['settings.navLabel'] })).toHaveCount(
						role === 'admin' ? 1 : 0
					);
					await page.screenshot({
						path: testInfo.outputPath(`account-${role}-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}
}

test('a signed-out /account is sent to the sign-in with next set to /account', async ({ page }) => {
	const started = startsSignIn(page, '%2Faccount');

	await page.goto(ACCOUNT).catch(() => undefined);

	await leftForSignIn(page, started);
});

test.describe('the header section', () => {
	test.use({ person: { role: 'admin' } });

	test('marks Decks as current on / and nothing on an unknown address', async ({ page }) => {
		const words = await wordsOf(page);
		const current = page.locator('header.top nav [aria-current]');

		await page.goto('/');
		await expect(current).toHaveText(words['header.decks']);

		await page.goto('/nope');
		await expect(page.locator('main h1')).toBeVisible();
		await expect(current).toHaveCount(0);
	});
});
