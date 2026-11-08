import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { addresses, close, created, open, press, type, words } from './addPageHarness.ts';

afterEach(close);

async function createdSource(): Promise<void> {
	open();
	type('source-name', 'talks');
	type('source-url', addresses.https.url);
	await vi.waitFor(() => expect(document.querySelector('#source-secret')).not.toBeNull());
	type('source-secret', 'an-access-token');
	press(words.source_check);
	await vi.waitFor(() => expect(document.body.textContent).toContain('head c0ffee1'));
	press(words.source_create);
	await vi.waitFor(() => expect(document.body.textContent).toContain(created.webhook_secret));
}

describe('the Add source page after a create', () => {
	it('shows the webhook secret once in place of the form', async () => {
		await createdSource();

		expect(document.querySelector('form')).toBeNull();
		expect(document.body.textContent).toContain(words.source_webhook_once);
	});

	it('forgets the webhook secret when the page is hidden', async () => {
		await createdSource();

		window.dispatchEvent(new Event('pagehide'));

		expect(document.body.textContent).not.toContain(created.webhook_secret);
		expect(document.querySelector('form')).not.toBeNull();
	});

	it('forgets what was typed on the form when the page is hidden', async () => {
		open();
		type('source-name', 'talks');
		type('source-url', addresses.https.url);
		await vi.waitFor(() => expect(document.querySelector('#source-secret')).not.toBeNull());
		type('source-secret', 'an-access-token');

		window.dispatchEvent(new Event('pagehide'));
		flushSync();

		for (const field of ['source-name', 'source-url']) {
			expect(document.querySelector<HTMLInputElement>(`#${field}`)?.value).toBe('');
		}
		expect(document.querySelector('#source-secret')).toBeNull();
	});
});
