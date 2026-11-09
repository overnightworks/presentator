import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	addresses,
	checkButton,
	close,
	createButton,
	deferredCheck,
	folderField,
	open,
	press,
	sentTo,
	type,
	words
} from './addPageHarness.ts';

afterEach(close);

const URL_FIELD = 'source-url';
const CHECK = '/api/sources/new/check';
const CREATE = '/api/sources';
const alert = (): Element | null => document.querySelector('[role="alert"]');
const status = (): Element | null => document.querySelector('[role="status"]');

async function checked(which: keyof typeof addresses, fetched = open()) {
	type(URL_FIELD, addresses[which].url);
	await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
	press(words.source_check);
	await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
	return fetched;
}

describe('what the address shows of the folder', () => {
	it('shows no folder field on an empty form', () => {
		open();

		expect(folderField()).toBeNull();
	});

	it.each([
		['https', true],
		['ssh', true],
		['folder', false],
		['none', false]
	] as const)('shows for a %s address the folder field %s', async (which, shown) => {
		open();

		type(URL_FIELD, addresses[which].url);

		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		expect(folderField() !== null).toBe(shown);
	});

	it('labels the folder field with the optional folder word', async () => {
		open();

		type(URL_FIELD, addresses.ssh.url);

		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		expect(document.querySelector('label[for="source-folder"]')?.textContent).toBe(
			words.source_folder_optional
		);
	});
});

describe('the folder on the wire', () => {
	it('sends the typed folder at Create and none at Check', async () => {
		const fetched = open();
		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		type('source-folder', 'examples');
		press(words.source_check);
		await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
		type('source-name', 'talks');

		createButton()?.click();

		await vi.waitFor(() => expect(sentTo(fetched, CREATE)).toHaveLength(1));
		expect(sentTo(fetched, CREATE)).toEqual([expect.objectContaining({ folder: 'examples' })]);
		expect(sentTo(fetched, CHECK)).toEqual([expect.not.objectContaining({ folder: expect.anything() })]);
	});

	it('sends an empty folder when none was typed', async () => {
		const fetched = await checked('https');
		type('source-name', 'talks');

		createButton()?.click();

		await vi.waitFor(() => expect(sentTo(fetched, CREATE)).toHaveLength(1));
		expect(sentTo(fetched, CREATE)).toEqual([expect.objectContaining({ folder: '' })]);
	});

	it('keeps the banner and Create when only the folder is edited after Check', async () => {
		await checked('https');

		type('source-folder', 'examples');
		flushSync();

		expect(status()?.textContent).toContain('c0ffee1');
		expect(createButton()?.disabled).toBe(false);
	});

	it('sends an empty folder once a folder typed for https meets a server folder', async () => {
		const fetched = open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		type('source-folder', 'examples');

		type(URL_FIELD, addresses.folder.url);
		await vi.waitFor(() => expect(folderField()).toBeNull());
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		press(words.source_check);
		await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
		type('source-name', 'talks');
		createButton()?.click();

		await vi.waitFor(() => expect(sentTo(fetched, CREATE)).toHaveLength(1));
		expect(sentTo(fetched, CREATE)).toEqual([expect.objectContaining({ folder: '' })]);
	});

	it('shows a fresh empty field when a key address returns after a server folder', async () => {
		open();
		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		type('source-folder', 'examples');
		type(URL_FIELD, addresses.folder.url);
		await vi.waitFor(() => expect(folderField()).toBeNull());

		type(URL_FIELD, addresses.ssh.url);

		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		expect(folderField()?.value).toBe('');
	});

	it('words a refused folder at Create', async () => {
		const fetched = await checked('https');
		fetched.mockImplementationOnce(async () =>
			Response.json(
				{
					type: 'tag:hallucinai.de,2026-09-08:presentator/problems/source-refused',
					reason: 'malformed-folder'
				},
				{ status: 422 }
			)
		);
		type('source-name', 'talks');

		createButton()?.click();

		await vi.waitFor(() => expect(alert()?.textContent).toContain(words.source_refused_folder));
	});
});

describe('the folder when the page is left through the cache', () => {
	it('leaves the folder empty when the same key address is typed again', async () => {
		open();
		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		type('source-folder', 'examples');

		window.dispatchEvent(new Event('pagehide'));
		type(URL_FIELD, addresses.ssh.url);

		await vi.waitFor(() => expect(folderField()).not.toBeNull());
		expect(folderField()?.value).toBe('');
	});
});

describe('a Check in flight when the address is edited', () => {
	it('writes neither banner nor fingerprint', async () => {
		const inFlight = deferredCheck();
		open(undefined, () => inFlight.answer);
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		press(words.source_check);

		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		await inFlight.reachable();
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(status()).toBeNull();
		expect(createButton()?.disabled).toBe(true);
	});

	it('writes no refusal when the stale Check was refused', async () => {
		const inFlight = deferredCheck();
		open(undefined, () => inFlight.answer);
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		press(words.source_check);

		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		inFlight.refusedWith('malformed-folder');
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(alert()).toBeNull();
		expect(createButton()?.disabled).toBe(true);
	});
});
