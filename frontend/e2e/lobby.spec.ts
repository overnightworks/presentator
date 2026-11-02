import type { Page } from '@playwright/test';

import type { SessionView } from '../src/lib/api/session.ts';
import { expect, test } from './csp.ts';

async function sessionOf(page: Page): Promise<SessionView> {
	const answer = await page.request.get('/api/session', { headers: { Accept: 'application/json' } });
	return (await answer.json()) as SessionView;
}

async function showsTheSignInBoard(page: Page, session: SessionView): Promise<void> {
	await expect(page.locator('.signin .wordmark')).toHaveText(session.words.wordmark);
	await expect(page.locator('.signin a[href]').first()).toHaveAttribute('href', session.sign_in.start);
}

async function showsAServerRenderedPage(page: Page, session: SessionView): Promise<void> {
	await expect(page.getByRole('link', { name: session.words.section_decks, exact: true })).toBeVisible();
	await expect(page.locator('.signin')).toHaveCount(0);
}

test.describe('signed out', () => {
	test.use({ storageState: { cookies: [], origins: [] } });

	test('the signed-out page renders the sign-in board from the kit', async ({ page }) => {
		const session = await sessionOf(page);

		await page.goto('/login');

		await showsTheSignInBoard(page, session);
		await expect(page).toHaveTitle(session.words.wordmark);
	});
});

const refusals = [
	{ path: '/login/no-access', action: 'log-out', retry: undefined },
	{ path: '/login/talk/no-access', action: 'log-out', retry: undefined },
	{ path: '/login/login-failed', action: 'retry', retry: (session: SessionView) => session.sign_in.start },
	{
		path: '/login/talk/login-failed',
		action: 'retry',
		retry: (session: SessionView) => session.sign_in.talk_start
	}
] as const;

for (const refusal of refusals) {
	test.describe(`${refusal.path}`, () => {
		test('answers 403 with the kit board, its legal links and the one way out', async ({ page }) => {
			const session = await sessionOf(page);

			const answer = await page.goto(refusal.path);

			expect(answer?.status()).toBe(403);
			await expect(page.locator('.signin .wordmark')).toHaveText(session.words.wordmark);
			await expect(page.getByRole('link', { name: session.words.footer_impressum })).toBeVisible();
			await expect(page.getByRole('link', { name: session.words.footer_datenschutz })).toBeVisible();
			if (refusal.action === 'log-out') {
				await expect(page.locator('form.log-out')).toHaveAttribute('action', session.sign_in.logout);
				await expect(page.locator('form.log-out')).toHaveAttribute('method', /post/i);
				await expect(page.getByRole('link', { name: 'Back' })).toHaveCount(0);
			} else {
				await expect(page.locator('.signin a[href]').first()).toHaveAttribute(
					'href',
					refusal.retry(session)
				);
			}
		});
	});
}

test("SvelteKit's announcer is there and visually hidden", async ({ page }) => {
	await page.goto('/login');

	const announcer = page.locator('#svelte-announcer');
	await expect(announcer).toHaveAttribute('aria-live', 'assertive');
	const box = await announcer.boundingBox();
	expect(box?.width).toBeLessThanOrEqual(1);
	expect(box?.height).toBeLessThanOrEqual(1);
});

test('Svelte, Jinja and Svelte again, then back and forward across them', async ({ page }) => {
	const session = await sessionOf(page);

	await page.goto('/login');
	await showsTheSignInBoard(page, session);
	await page.goto('/');
	await showsAServerRenderedPage(page, session);
	await page.goto('/login');
	await showsTheSignInBoard(page, session);

	await page.goBack();
	await showsAServerRenderedPage(page, session);
	await page.goBack();
	await showsTheSignInBoard(page, session);
	await page.goForward();
	await showsAServerRenderedPage(page, session);
	await page.goForward();
	await showsTheSignInBoard(page, session);
});

test('back and forward across a Jinja page and a refusal land on the right page', async ({ page }) => {
	const session = await sessionOf(page);

	await page.goto('/');
	await showsAServerRenderedPage(page, session);
	await page.goto('/login/login-failed');
	await expect(page.locator('.signin h1')).toHaveText('Login failed');

	await page.goBack();
	await showsAServerRenderedPage(page, session);
	await page.goForward();
	await expect(page.locator('.signin h1')).toHaveText('Login failed');
});

test('a link to a page the app does not own leaves the app by a page load', async ({ page }) => {
	const session = await sessionOf(page);
	await page.route(`**${session.sign_in.start}`, (route) =>
		route.fulfill({ contentType: 'text/plain', body: 'the sign-in start' })
	);
	await page.goto('/login');

	const [request] = await Promise.all([
		page.waitForRequest((sent) => sent.url().endsWith(session.sign_in.start)),
		page.locator('.signin a[href]').first().click()
	]);

	expect(request.isNavigationRequest()).toBe(true);
	await expect(page.locator('body')).toHaveText('the sign-in start');
});

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		test(`the sign-in board at ${width} px, ${colorScheme}`, async ({ page }, testInfo) => {
			const session = await sessionOf(page);
			await page.setViewportSize({ width, height: 844 });
			await page.emulateMedia({ colorScheme });

			await page.goto('/login');
			await showsTheSignInBoard(page, session);
			await page.evaluate(() => document.fonts.ready);

			await page.screenshot({
				path: testInfo.outputPath(`login-${width}-${colorScheme}.png`),
				fullPage: true
			});
		});
	}
}
