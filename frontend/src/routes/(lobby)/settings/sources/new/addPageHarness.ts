import { flushSync, mount, unmount } from 'svelte';
import { vi } from 'vitest';

import type { AccessKind } from '#lib/api/sources.ts';
import { navigation } from '#lib/navigation.ts';
import { words } from '#lib/words/en.ts';
import Page from './+page.svelte';

export { words };
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
export type CheckAnswer = () => Response | Promise<Response>;

const reachable: CheckAnswer = () =>
	Response.json({ commit: 'c0ffee1', detail: null, failure: null, fingerprint: 'fp-1' });

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
export function open(
	kind: KindAnswer = answeredByAddress,
	check: CheckAnswer = reachable,
	create: CheckAnswer = () => Response.json(created, { status: 201 })
) {
	vi.spyOn(navigation, 'goto').mockResolvedValue(undefined);
	const fetched = vi.fn(async (path: string, init?: RequestInit) => {
		if (path === '/api/sources/new/kind') {
			return kind((JSON.parse(init?.body as string) as { url: string }).url);
		}
		if (path === '/api/sources/new/check') {
			return check();
		}
		if (path === '/api/sources') {
			return create();
		}
		return Response.json(draft);
	});
	vi.stubGlobal('fetch', fetched);
	mounted.add(
		mount(Page, {
			target: document.body,
			props: { data: { words, draft } } as never
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
export const folderField = (): HTMLInputElement | null =>
	document.querySelector<HTMLInputElement>('#source-folder');
export const keyBlock = (): Element | null => document.getElementById('deploy-key-label');
// Found by role and place: a busy button carries another word than its idle one.
export const checkButton = (): HTMLButtonElement | null =>
	document.querySelector<HTMLButtonElement>('.actions button[type="button"]');
export const createButton = (): HTMLButtonElement | null =>
	document.querySelector<HTMLButtonElement>('.actions button[type="submit"]');

/** A lookup whose answer the scenario gives when it chooses. */
export function deferred() {
	let settle: (answer: Response) => void = () => undefined;
	const answer = new Promise<Response>((resolve) => {
		settle = resolve;
	});
	return { answer, answerWith: (access: AccessKind | null) => settle(Response.json({ access })) };
}

/** A Check whose answer the scenario gives when it chooses. */
export function deferredCheck() {
	let settle: (answer: Response) => void = () => undefined;
	const answer = new Promise<Response>((resolve) => {
		settle = resolve;
	});
	return {
		answer,
		reachable: () => settle(reachable() as Response),
		created: () => settle(Response.json(created, { status: 201 })),
		refusedWith: (reason: string) =>
			settle(
				Response.json(
					{ type: 'tag:hallucinai.de,2026-09-08:presentator/problems/source-refused', reason },
					{ status: 422 }
				)
			)
	};
}
