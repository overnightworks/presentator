import type { BrowserContext, Page } from '@playwright/test';

import type { SessionView } from '../src/lib/api/session.ts';
import { expect } from './person.ts';

// The scenario's clock; each deck's `changed_at` is this instant less its age.
export const NOW = '2026-11-03T12:00:00Z';
export const minutesAgo = (minutes: number): string =>
	new Date(Date.parse(NOW) - minutes * 60_000).toISOString();

export const SIGN_IN_START = '/login/oidc';
export const MENU = '#account-menu';

export async function sessionOf(page: Page): Promise<SessionView> {
	const answer = await page.request.get('/api/session', {
		headers: { Accept: 'application/json' }
	});
	return (await answer.json()) as SessionView;
}

export async function opensTheMenu(page: Page): Promise<void> {
	await page.locator('button.account').click();
	await expect(page.locator(MENU)).toBeVisible();
}

export function startsSignIn(page: Page, next = '%2F') {
	return page.waitForRequest((sent) => {
		const address = new URL(sent.url());
		return address.pathname === SIGN_IN_START && address.search === `?next=${next}`;
	});
}

// The sign-in's address is an issuer nobody answers in the drive, so the page
// is parked once the request was seen, and no navigation is left in flight.
export async function leftForSignIn(page: Page, started: ReturnType<typeof startsSignIn>): Promise<void> {
	await started;
	// The failed load of the issuer's address may land first; either way the page is parked.
	await page.goto('about:blank').catch(() => undefined);
}

export const entriesOf = (page: Page): Promise<number> => page.evaluate(() => history.length);

export async function chooseTheme(page: Page, name: 'Follow system' | 'Light' | 'Dark'): Promise<void> {
	await opensTheMenu(page);
	await page.getByRole('radio', { name }).click();
}

// Presses Tab and returns the focused control's name after asserting it shows a ring.
export async function tabbedControlWithRing(page: Page): Promise<string | null> {
	await page.keyboard.press('Tab');
	const ring = await page.evaluate(() => {
		const active = document.activeElement;
		if (!active || active === document.body) {
			return null;
		}
		const style = getComputedStyle(active);
		return {
			control:
				active.textContent?.trim() ||
				(active as HTMLInputElement).labels?.[0]?.textContent?.trim() ||
				active.className,
			style: style.outlineStyle,
			width: parseFloat(style.outlineWidth)
		};
	});
	if (!ring) {
		return null;
	}
	expect(ring.style, ring.control).not.toBe('none');
	expect(ring.width, ring.control).toBeGreaterThanOrEqual(2);
	return ring.control;
}

export async function endsTheSession(context: BrowserContext): Promise<void> {
	await context.clearCookies();
}
