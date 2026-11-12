import { flushSync } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
	addresses,
	checkButton,
	close,
	createButton,
	deferred,
	deferredCheck,
	keyBlock,
	open,
	press,
	secretField,
	sentTo,
	type,
	words
} from './addPageHarness.ts';

afterEach(close);

const URL_FIELD = 'source-url';
const KIND = '/api/sources/new/kind';
const CHECK = '/api/sources/new/check';
const status = (): Element | null => document.querySelector('[role="status"]');
const alert = (): Element | null => document.querySelector('[role="alert"]');

describe('what the address shows', () => {
	it('shows neither the token field nor the key on an empty form and sends nothing', () => {
		const fetched = open();

		expect(secretField()).toBeNull();
		expect(keyBlock()).toBeNull();
		expect(checkButton()?.disabled).toBe(true);
		expect(sentTo(fetched, KIND)).toEqual([]);
	});

	it.each([
		['https', true, false],
		['ssh', false, true],
		['folder', false, false],
		['none', false, false]
	] as const)('shows for a %s address the token field %s and the key %s', async (which, token, key) => {
		open();

		type(URL_FIELD, addresses[which].url);

		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		expect(secretField() !== null).toBe(token);
		expect(keyBlock() !== null).toBe(key);
	});

	it('asks for the address as typed and never for a blank one', async () => {
		const fetched = open(() => Response.json({ access: 'https-token' }));

		type(URL_FIELD, `${addresses.https.url} `);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		type(URL_FIELD, '   ');
		flushSync();

		expect(sentTo(fetched, KIND)).toEqual([{ url: `${addresses.https.url} ` }]);
		expect(secretField()).toBeNull();
		expect(checkButton()?.disabled).toBe(true);
	});

	it('keeps the token field a new-password field so a browser fills nothing into it', async () => {
		open();

		type(URL_FIELD, addresses.https.url);

		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		expect(secretField()?.getAttribute('autocomplete')).toBe('new-password');
		expect(secretField()?.type).toBe('password');
	});
});

describe('the secret on the wire', () => {
	it('sends the token typed for an https address', async () => {
		const fetched = open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		type('source-secret', 'a-typed-token');

		checkButton()?.click();

		await vi.waitFor(() => expect(sentTo(fetched, CHECK)).toHaveLength(1));
		expect(sentTo(fetched, CHECK)).toEqual([expect.objectContaining({ secret: 'a-typed-token' })]);
	});

	it.each(['ssh', 'folder'] as const)(
		'sends no secret once a typed token meets a %s address',
		async (which) => {
			const fetched = open();
			type(URL_FIELD, addresses.https.url);
			await vi.waitFor(() => expect(secretField()).not.toBeNull());
			type('source-secret', 'a-typed-token');

			type(URL_FIELD, addresses[which].url);
			await vi.waitFor(() => expect(secretField()).toBeNull());
			await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
			checkButton()?.click();

			await vi.waitFor(() => expect(sentTo(fetched, CHECK)).toHaveLength(1));
			expect(sentTo(fetched, CHECK)).toEqual([expect.objectContaining({ secret: null })]);
		}
	);

	it('shows an empty token field when https returns after a key address', async () => {
		open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		type('source-secret', 'a-typed-token');
		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(keyBlock()).not.toBeNull());

		type(URL_FIELD, addresses.https.url);

		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		expect(secretField()?.value).toBe('');
	});
});

describe('the lookup is latest wins', () => {
	it('follows the current address when the answers arrive out of order', async () => {
		const first = deferred();
		const second = deferred();
		const lookups = [first, second];
		open(() => (lookups.shift() as ReturnType<typeof deferred>).answer);
		type(URL_FIELD, addresses.https.url);
		type(URL_FIELD, addresses.ssh.url);

		second.answerWith('ssh-deploy-key');
		await vi.waitFor(() => expect(keyBlock()).not.toBeNull());
		first.answerWith('https-token');
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(keyBlock()).not.toBeNull();
		expect(secretField()).toBeNull();
	});

	it('ignores the failure of a lookup a newer address replaced', async () => {
		let breakFirst: (reason: Error) => void = () => undefined;
		const first = new Promise<Response>((_, reject) => {
			breakFirst = reject;
		});
		const second = deferred();
		const lookups = [first, second.answer];
		open(() => lookups.shift() as Promise<Response>);
		type(URL_FIELD, addresses.https.url);
		type(URL_FIELD, addresses.ssh.url);

		breakFirst(new Error('network down'));
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(alert()).toBeNull();
		second.answerWith('ssh-deploy-key');
		await vi.waitFor(() => expect(keyBlock()).not.toBeNull());
	});

	it('shows no token field while the lookup for a key address is pending', async () => {
		const pending = deferred();
		let first = true;
		open(() => {
			if (first) {
				first = false;
				return Response.json({ access: 'https-token' });
			}
			return pending.answer;
		});
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());

		type(URL_FIELD, addresses.ssh.url);
		flushSync();

		expect(secretField()).toBeNull();
		expect(status()?.textContent).toBe(words['sourceNew.kindPending']);
		expect(checkButton()?.disabled).toBe(true);
	});

	it('drops an answer that arrives after the form was reset', async () => {
		const late = deferred();
		open(() => late.answer);
		type(URL_FIELD, addresses.https.url);

		window.dispatchEvent(new Event('pagehide'));
		late.answerWith('https-token');
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(secretField()).toBeNull();
		expect(checkButton()?.disabled).toBe(true);
	});

	it('drops the answer of a lookup the reset cut off when the same text is typed again', async () => {
		const first = deferred();
		const second = deferred();
		const lookups = [first, second];
		open(() => (lookups.shift() as ReturnType<typeof deferred>).answer);
		type(URL_FIELD, addresses.https.url);
		window.dispatchEvent(new Event('pagehide'));
		type(URL_FIELD, addresses.https.url);

		first.answerWith('https-token');
		await new Promise((resolve) => setTimeout(resolve));
		flushSync();

		expect(secretField()).toBeNull();
		expect(status()).not.toBeNull();
		second.answerWith('ssh-deploy-key');
		await vi.waitFor(() => expect(keyBlock()).not.toBeNull());
	});

	it('leaves the form and its answer empty when the page is left through the cache', async () => {
		open();
		type('source-name', 'talks');
		type(URL_FIELD, addresses.ssh.url);
		await vi.waitFor(() => expect(keyBlock()).not.toBeNull());

		window.dispatchEvent(new Event('pagehide'));
		flushSync();

		expect(keyBlock()).toBeNull();
		expect(secretField()).toBeNull();
		expect(document.querySelector<HTMLInputElement>('#source-name')?.value).toBe('');
		expect(document.querySelector<HTMLInputElement>(`#${URL_FIELD}`)?.value).toBe('');
	});
});

describe('a check result belongs to the address it was for', () => {
	it('forgets the token a hidden page held when the same https address is typed again', async () => {
		open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		type('source-secret', 'a-typed-token');

		window.dispatchEvent(new Event('pagehide'));
		type(URL_FIELD, addresses.https.url);

		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		expect(secretField()?.value).toBe('');
	});

	it('takes the reachable banner and the enabled Create away when the address is edited', async () => {
		open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
		checkButton()?.click();
		await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
		expect(status()?.textContent).toContain('c0ffee1');

		type(URL_FIELD, addresses.ssh.url);
		flushSync();

		expect(status()?.textContent).toBe(words['sourceNew.kindPending']);
		expect(createButton()?.disabled).toBe(true);
	});

	it('keeps the reachable banner and Create when only the token is edited', async () => {
		open();
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		checkButton()?.click();
		await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));

		type('source-secret', 'a-typed-token');
		flushSync();

		expect(status()?.textContent).toContain('c0ffee1');
		expect(createButton()?.disabled).toBe(false);
	});
});

describe('a request in flight', () => {
	it.each(['check', 'create'] as const)(
		'shows the %s button busy by its word while its answer is held, then reverts',
		async (which) => {
			const held = deferredCheck();
			open(undefined, which === 'check' ? () => held.answer : undefined, () => held.answer);
			type('source-name', 'talks');
			type(URL_FIELD, addresses.https.url);
			await vi.waitFor(() => expect(checkButton()?.disabled).toBe(false));
			const pressed = which === 'check' ? checkButton : createButton;
			if (which === 'create') {
				checkButton()?.click();
				await vi.waitFor(() => expect(createButton()?.disabled).toBe(false));
			}

			pressed()?.click();
			flushSync();

			const word = which === 'check' ? words['sourceNew.busyCheck'] : words['sourceNew.busyCreate'];
			expect(pressed()?.getAttribute('aria-busy')).toBe('true');
			expect(pressed()?.textContent?.trim()).toBe(word);
			expect(pressed()?.querySelector('.state[data-state="building"]')).not.toBeNull();
			expect(checkButton()?.disabled).toBe(true);
			expect(createButton()?.disabled).toBe(true);

			if (which === 'check') {
				held.reachable();
				await vi.waitFor(() => expect(pressed()?.hasAttribute('aria-busy')).toBe(false));
				expect(pressed()?.textContent?.trim()).toBe(words['sourceNew.check']);
			} else {
				held.created();
				await vi.waitFor(() => expect(document.body.textContent).toContain(words['sourceNew.created']));
			}
		}
	);
});

describe('a lookup that fails', () => {
	it.each([500, 403, 404])(
		'says so with a retry and shows no field when the answer is %s',
		async (code) => {
			open(() => new Response(null, { status: code }));

			type(URL_FIELD, addresses.https.url);

			await vi.waitFor(() => expect(alert()?.textContent).toContain(words['sourceNew.kindFailed']));
			expect(secretField()).toBeNull();
			expect(keyBlock()).toBeNull();
			expect(checkButton()?.disabled).toBe(true);
			expect(status()).toBeNull();
		}
	);

	it('recovers when the retry is answered', async () => {
		let healthy = false;
		open(() =>
			healthy ? Response.json({ access: 'https-token' }) : new Response(null, { status: 500 })
		);
		type(URL_FIELD, addresses.https.url);
		await vi.waitFor(() => expect(alert()).not.toBeNull());
		healthy = true;

		press(words['sourceNew.kindRetry']);

		await vi.waitFor(() => expect(secretField()).not.toBeNull());
		expect(alert()).toBeNull();
		expect(checkButton()?.disabled).toBe(false);
	});
});
