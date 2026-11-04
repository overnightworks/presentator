import { flushSync, mount, unmount } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LobbyText } from '#lib/api/session.ts';
import { navigation } from '#lib/navigation.ts';
import Page from './+page.svelte';

// A scenario's own templates: none of them is the catalog's sentence.
const words = {
	sources_add: 'Plant a source',
	source_name: 'Handle',
	source_url: 'Where',
	source_secret: 'Passphrase',
	source_check: 'Probe',
	source_create: 'Plant',
	source_created: 'is planted',
	source_webhook_once: 'seen this once',
	source_check_reachable: 'head {commit}'
} as LobbyText;
const draft = { id: 'draft-1', public_key: 'a-public-key' };
const created = {
	name: 'talks',
	hook_url: 'https://lobby.test/sources/talks/fetch',
	webhook_secret: 'whs-the-scenarios-own-secret'
};

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (mounted) {
		void unmount(mounted);
	}
	mounted = undefined;
	document.body.replaceChildren();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

function answers(): void {
	vi.stubGlobal(
		'fetch',
		vi.fn(async (path: string) => {
			if (path === '/api/sources/new/check') {
				return Response.json({
					commit: 'c0ffee1',
					detail: null,
					failure: null,
					fingerprint: 'fp-1'
				});
			}
			if (path === '/api/sources') {
				return Response.json(created, { status: 201 });
			}
			return Response.json(draft);
		})
	);
}

function open(): void {
	vi.spyOn(navigation, 'goto').mockResolvedValue(undefined);
	answers();
	mounted = mount(Page, {
		target: document.body,
		props: { data: { session: { words }, draft } } as never
	});
	flushSync();
}

function type(label: string, text: string): void {
	const field = document.querySelector<HTMLInputElement>(`input[id="${label}"]`);
	if (!field) {
		throw new Error(`no field ${label}`);
	}
	field.value = text;
	field.dispatchEvent(new Event('input', { bubbles: true }));
}

function press(label: string): void {
	const button = [...document.querySelectorAll('button')].find(
		(each) => each.textContent === label
	);
	button?.click();
}

async function createdSource(): Promise<void> {
	open();
	type('source-name', 'talks');
	type('source-url', 'https://git.test/talks.git');
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

	it('forgets what was typed on the form when the page is hidden', () => {
		open();
		type('source-name', 'talks');
		type('source-url', 'https://git.test/talks.git');
		type('source-secret', 'an-access-token');
		const ssh = document.querySelector<HTMLInputElement>('input[value="ssh-deploy-key"]');
		if (ssh === null) {
			throw new Error('no ssh-deploy-key radio');
		}
		ssh.checked = true;
		ssh.dispatchEvent(new Event('change', { bubbles: true }));
		flushSync();

		window.dispatchEvent(new Event('pagehide'));
		flushSync();

		for (const field of ['source-name', 'source-url', 'source-secret']) {
			expect(document.querySelector<HTMLInputElement>(`#${field}`)?.value).toBe('');
		}
		expect(document.querySelector<HTMLInputElement>('input[value="https-token"]')?.checked).toBe(true);
	});
});
