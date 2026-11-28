import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	addresses,
	checked,
	checkButton,
	close,
	createButton,
	open,
	ownerField,
	sentTo,
	type
} from './addPageHarness.ts';

afterEach(close);

const URL_FIELD = 'source-url';
const OWNER_FIELD = 'source-owner';
const CHECK = '/api/sources/new/check';
const CREATE = '/api/sources';

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
});

describe('the owner when the page is left through the cache', () => {
	it('leaves the owner empty', () => {
		open();
		type(OWNER_FIELD, 'anna');

		window.dispatchEvent(new Event('pagehide'));

		expect(ownerField()?.value).toBe('');
	});
});
