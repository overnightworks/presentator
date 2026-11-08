import { flushSync, mount, unmount } from 'svelte';
import { vi } from 'vitest';

import type { AccessKind } from '#lib/api/sources.ts';
import type { LobbyText } from '#lib/api/session.ts';
import { navigation } from '#lib/navigation.ts';
import Page from './+page.svelte';

// A scenario's own templates: none of them is the catalog's sentence.
export const words = {
	sources_add: 'Plant a source',
	source_name: 'Handle',
	source_url: 'Where',
	source_secret: 'Passphrase',
	source_check: 'Probe',
	source_create: 'Plant',
	source_created: 'is planted',
	source_webhook_once: 'seen this once',
	source_check_reachable: 'head {commit}',
	source_kind_pending: 'reading it',
	source_kind_failed: 'could not read it',
	source_kind_retry: 'once more',
	source_deploy_key_heading: 'the key to place'
} as LobbyText;
export const draft = { id: 'draft-1', public_key: 'a-public-key' };
export const created = {
	name: 'talks',
	hook_url: 'https://lobby.test/sources/talks/fetch',
	webhook_secret: 'whs-the-scenarios-own-secret'
};
export const addresses = {
	https: { url: 'https://git.test/talks.git', access: 'https-token' },
	ssh: { url: 'git@git.test:talks.git', access: 'ssh-deploy-key' },
	folder: { url: '/srv/talks', access: 'local-folder' },
	none: { url: 'ftp://git.test/talks.git', access: null }
} as const satisfies Record<string, { url: string; access: AccessKind | null }>;

export type KindAnswer = (url: string) => Response | Promise<Response>;

const answeredByAddress: KindAnswer = (url) =>
	Response.json({
		access: Object.values(addresses).find((each) => each.url === url)?.access ?? null
	});

const mounted = new Set<ReturnType<typeof mount>>();

export function close(): void {
	mounted.forEach((each) => void unmount(each));
	mounted.clear();
	document.body.replaceChildren();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
}

/** Mounts the page; the returned mock is every request it sent. */
export function open(kind: KindAnswer = answeredByAddress) {
	vi.spyOn(navigation, 'goto').mockResolvedValue(undefined);
	const fetched = vi.fn(async (path: string, init?: RequestInit) => {
		if (path === '/api/sources/new/kind') {
			return kind((JSON.parse(init?.body as string) as { url: string }).url);
		}
		if (path === '/api/sources/new/check') {
			return Response.json({ commit: 'c0ffee1', detail: null, failure: null, fingerprint: 'fp-1' });
		}
		if (path === '/api/sources') {
			return Response.json(created, { status: 201 });
		}
		return Response.json(draft);
	});
	vi.stubGlobal('fetch', fetched);
	mounted.add(
		mount(Page, {
			target: document.body,
			props: { data: { session: { words }, draft } } as never
		})
	);
	flushSync();
	return fetched;
}

export function type(label: string, text: string): void {
	const field = document.querySelector(`input[id="${label}"]`) as HTMLInputElement;
	field.value = text;
	field.dispatchEvent(new Event('input', { bubbles: true }));
}

export function press(label: string): void {
	const button = [...document.querySelectorAll('button')].find(
		(each) => each.textContent === label
	) as HTMLButtonElement;
	button.click();
}

/** The body of each request the page sent to this path. */
export function sentTo(fetched: ReturnType<typeof open>, path: string): unknown[] {
	return fetched.mock.calls
		.filter(([sent]) => sent === path)
		.map(([, init]) => JSON.parse(init?.body as string));
}

export const secretField = (): HTMLInputElement | null =>
	document.querySelector<HTMLInputElement>('#source-secret');
export const keyBlock = (): Element | null => document.getElementById('deploy-key-label');
export const checkButton = (): HTMLButtonElement | undefined =>
	[...document.querySelectorAll('button')].find((each) => each.textContent === words.source_check);
export const createButton = (): HTMLButtonElement | undefined =>
	[...document.querySelectorAll('button')].find((each) => each.textContent === words.source_create);

/** A lookup whose answer the scenario gives when it chooses. */
export function deferred() {
	let settle: (answer: Response) => void = () => undefined;
	const answer = new Promise<Response>((resolve) => {
		settle = resolve;
	});
	return { answer, answerWith: (access: AccessKind | null) => settle(Response.json({ access })) };
}
