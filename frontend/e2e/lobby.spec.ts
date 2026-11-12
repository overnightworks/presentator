import type { Page } from '@playwright/test';

import { expect, test } from './person.ts';
import { type Reader, readerOf } from './scenario.ts';

const SOURCES_PAGE = '/settings/sources';

async function showsTheSignInBoard(page: Page, session: Reader): Promise<void> {
	await expect(page.locator('.signin .wordmark')).toHaveText(session.words['app.name']);
	await expect(page.locator('.signin a[href]').first()).toHaveAttribute(
		'href',
		session.sign_in.start
	);
}

async function showsALobbyPage(page: Page, session: Reader): Promise<void> {
	await expect(
		page.getByRole('link', { name: session.words['header.decks'], exact: true })
	).toBeVisible();
	await expect(page.locator('.signin')).toHaveCount(0);
}

// SI2.
test('the signed-out page renders the sign-in board from the kit', async ({ page }) => {
	const session = await readerOf(page);

	await page.goto('/login');

	await showsTheSignInBoard(page, session);
	await expect(page).toHaveTitle(session.words['app.name']);
});

const refusals = [
	{ path: '/login/no-access', action: 'log-out', retry: undefined },
	{ path: '/login/talk/no-access', action: 'log-out', retry: undefined },
	{
		path: '/login/login-failed',
		action: 'retry',
		retry: (session: Reader) => session.sign_in.start
	},
	{
		path: '/login/talk/login-failed',
		action: 'retry',
		retry: (session: Reader) => session.sign_in.talk_start
	}
] as const;

async function showsTheRefusalBoard(
	page: Page,
	session: Reader,
	refusal: (typeof refusals)[number]
): Promise<void> {
	await expect(page.locator('.signin .wordmark')).toHaveText(session.words['app.name']);
	await expect(page.getByRole('link', { name: session.words['legal.impressum'] })).toBeVisible();
	await expect(page.getByRole('link', { name: session.words['legal.datenschutz'] })).toBeVisible();
	await expect(page.getByRole('link', { name: session.words['legal.nutzungsbedingungen'] })).toBeVisible();
	if (refusal.action === 'log-out') {
		await expect(page.locator('form.log-out')).toHaveAttribute('action', session.sign_in.logout);
		await expect(page.locator('form.log-out')).toHaveAttribute('method', /post/i);
		await expect(page.getByRole('link', { name: 'Back' })).toHaveCount(0);
	} else {
		await expect(page.locator('.signin [role=alert]')).toBeVisible();
		await expect(page.locator('.signin a[href]').first()).toHaveAttribute(
			'href',
			refusal.retry(session)
		);
	}
}

test("SvelteKit's announcer is there and visually hidden", async ({ page }) => {
	await page.goto('/login');

	const announcer = page.locator('#svelte-announcer');
	await expect(announcer).toHaveAttribute('aria-live', 'assertive');
	const box = await announcer.boundingBox();
	expect(box?.width).toBeLessThanOrEqual(1);
	expect(box?.height).toBeLessThanOrEqual(1);
});

test.describe('signed in as an admin', () => {
	test.use({ person: { role: 'admin' } });

	test('back and forward across a lobby page and a refusal land on the right page', async ({
		page
	}) => {
		const session = await readerOf(page);

		await page.goto(SOURCES_PAGE);
		await showsALobbyPage(page, session);
		await page.goto('/login/login-failed');
		await expect(page).toHaveURL(/\/login\/login-failed$/);
		await expect(page.locator('.signin [role=alert]')).toBeVisible();

		await page.goBack();
		await showsALobbyPage(page, session);
		await page.goForward();
		await expect(page).toHaveURL(/\/login\/login-failed$/);
		await expect(page.locator('.signin [role=alert]')).toBeVisible();
	});
});

test('a link to a page the app does not own leaves the app by a page load', async ({ page }) => {
	const session = await readerOf(page);
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

const boards = [
	{ path: '/login', name: 'login' },
	...refusals.map((refusal) => ({
		path: refusal.path,
		name: refusal.path.slice(1).replaceAll('/', '-'),
		refusal
	}))
];

for (const colorScheme of ['light', 'dark'] as const) {
	for (const width of [390, 1280]) {
		for (const board of boards) {
			test.describe(`${board.path} at ${width} px, ${colorScheme}`, () => {
				// SI12.
				test('shows its board, answers 403 on a refusal address and saves a screenshot', async ({
					page
				}, testInfo) => {
					const session = await readerOf(page);
					await page.setViewportSize({ width, height: 844 });
					await page.emulateMedia({ colorScheme });

					const answer = await page.goto(board.path);
					if ('refusal' in board) {
						expect(answer?.status()).toBe(403);
						await showsTheRefusalBoard(page, session, board.refusal);
					} else {
						await showsTheSignInBoard(page, session);
					}
					await page.evaluate(() => document.fonts.ready);

					await page.screenshot({
						path: testInfo.outputPath(`${board.name}-${width}-${colorScheme}.png`),
						fullPage: true
					});
				});
			});
		}
	}
}
