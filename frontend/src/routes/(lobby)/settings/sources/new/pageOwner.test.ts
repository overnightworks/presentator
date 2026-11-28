import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	addresses,
	checkButton,
	close,
	createButton,
	open,
	ownerField,
	sentTo,
	type,
	words
} from './addPageHarness.ts';

afterEach(close);

const URL_FIELD = 'source-url';
const OWNER_FIELD = 'source-owner';
const CHECK = '/api/sources/new/check';
const CREATE = '/api/sources';
const alert = (): Element | null => document.querySelector('[role="alert"]');

async function checked(which: keyof typeof addresses, fetched = open()) {
	type(URL_FIELD, addresses[which].url);
	await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
	checkButton()?.click();
	await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
	return fetched;
}

describe('the owner field', () => {
	it.each([
		['an empty form', undefined],
		['an https address', 'https'],
		['an ssh address', 'ssh'],
		['an address with no access', 'none']
	] as const)('is shown on %s', async (_case, which) => {
		open();

		if (which !== undefined) {
			type(URL_FIELD, addresses[which].url);
			await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		}

		expect(ownerField()).not.toBeNull();
	});

	it('is labelled and explained by the catalog words', () => {
		open();

		expect(document.querySelector('label[for="source-owner"]')?.textContent).toBe(
			words['sourceNew.owner']
		);
		expect(document.getElementById('source-owner-hint')?.textContent).toBe(
			words['sourceNew.ownerHint']
		);
		expect(ownerField()?.getAttribute('aria-describedby')).toBe('source-owner-hint');
	});
});

describe('the owner on the wire', () => {
	it.each([
		['https', 'anna'],
		['ssh', 'anna'],
		['https', '']
	] as const)('sends at Create the owner typed beside a %s address: "%s"', async (which, owner) => {
		const fetched = await checked(which);
		type(OWNER_FIELD, owner);
		type('source-name', 'talks');

		createButton()?.click();

		await vi.waitFor(() => expect(sentTo(fetched, CREATE)).toHaveLength(1));
		expect(sentTo(fetched, CREATE)).toEqual([expect.objectContaining({ owner })]);
		expect(sentTo(fetched, CHECK)).toEqual([expect.not.objectContaining({ owner: expect.anything() })]);
	});

	it('words a refused owner at Create and keeps what was typed', async () => {
		const fetched = await checked('https');
		fetched.mockImplementationOnce(async () =>
			Response.json(
				{
					type: 'tag:hallucinai.de,2026-09-08:presentator/problems/source-refused',
					reason: 'unknown-owner'
				},
				{ status: 422 }
			)
		);
		type(OWNER_FIELD, 'anan');
		type('source-name', 'talks');

		createButton()?.click();

		await vi.waitFor(() => expect(alert()?.textContent).toContain(words['source.refusedOwner']));
		expect(ownerField()?.value).toBe('anan');
		expect(createButton()?.disabled).toBe(false);
	});
});

describe('the owner when the page is left through the cache', () => {
	it('leaves the owner empty', () => {
		open();
		type(OWNER_FIELD, 'anna');

		window.dispatchEvent(new Event('pagehide'));

		expect(ownerField()?.value).toBe('');
	});
});
