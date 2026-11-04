import { expect, test } from './person.ts';
import { leftForSignIn, sessionOf, startsSignIn } from './scenario.ts';

// Cell 1.
for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test.describe(`an unknown address at ${width} px, ${colorScheme}`, () => {
			test.use({ person: { role: 'admin' }, colorScheme });

			test('shows the not-found board inside the lobby with its header at 200', async ({
				page
			}, testInfo) => {
				const { words } = await sessionOf(page);
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

// The address of the page this lobby once had for a person's own choices.
const ACCOUNT = '/account';

// Cells 1 and 2.
for (const role of ['admin', 'user'] as const) {
	for (const colorScheme of ['light', 'dark'] as const) {
		for (const width of [390, 1280]) {
			test.describe(`/account of ${role === 'admin' ? 'an admin' : 'a member'} at ${width} px, ${colorScheme}`, () => {
				test.use({ person: { role }, colorScheme });

				test('is the not-found board with the header and no form', async ({ page }, testInfo) => {
					const { words } = await sessionOf(page);
					await page.setViewportSize({ width, height: 900 });

					const answer = await page.goto(ACCOUNT);

					expect(answer?.status()).toBe(200);
					await expect(page.locator('main h1')).toHaveText(words.address_unknown_title);
					await expect(page.locator('header.top .wordmark')).toHaveText(words.wordmark);
					await expect(page.locator('main select, main form')).toHaveCount(0);
					await expect(page.getByRole('link', { name: words.section_settings })).toHaveCount(
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

// Cell 3.
test('a signed-out /account is sent to the sign-in with next set to /account', async ({ page }) => {
	const started = startsSignIn(page, '%2Faccount');

	await page.goto(ACCOUNT).catch(() => undefined);

	await leftForSignIn(page, started);
});

test.describe('the header section', () => {
	test.use({ person: { role: 'admin' } });

	// Cell 3.
	test('marks Decks as current on / and nothing on an unknown address', async ({ page }) => {
		const { words } = await sessionOf(page);
		const current = page.locator('header.top nav [aria-current]');

		await page.goto('/');
		await expect(current).toHaveText(words.section_decks);

		await page.goto('/nope');
		await expect(page.locator('main h1')).toBeVisible();
		await expect(current).toHaveCount(0);
	});
});
