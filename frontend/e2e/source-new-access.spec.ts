import { expect, test } from './person.ts';
import { expectsNoHorizontalScroll, leftForSignIn, sessionOf, startsSignIn } from './scenario.ts';
import { KEY_DRAFT_API, NEW_ADDRESS, SAME_ORIGIN } from './sources.ts';

const NEXT = '%2Fsettings%2Fsources%2Fnew';

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
