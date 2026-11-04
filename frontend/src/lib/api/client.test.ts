import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { isHttpError } from '@sveltejs/kit';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { getJson, postJson, putJson, Refused } from '#lib/api/client.ts';

function answering(status: number, body: unknown): { fetcher: typeof fetch; asked: Request[] } {
	const asked: Request[] = [];
	const fetcher: typeof fetch = async (input, init) => {
		asked.push(new Request(new URL(String(input), 'http://lobby.test'), init));
		return new Response(JSON.stringify(body), { status });
	};
	return { fetcher, asked };
}

afterEach(() => {
	vi.restoreAllMocks();
});

describe('getJson', () => {
	it('asks for JSON, so the session gate answers 401 instead of a sign-in redirect', async () => {
		const { fetcher, asked } = answering(200, {});
		await getJson(fetcher, '/api/session');
		expect(asked[0]?.headers.get('accept')).toBe('application/json');
	});

	it('returns the answer the route sent', async () => {
		const { fetcher } = answering(200, { theme: 'dark' });
		await expect(getJson(fetcher, '/api/session')).resolves.toEqual({ theme: 'dark' });
	});

	it('fails loud on a refusal that is not a 401', async () => {
		const { fetcher } = answering(500, {});
		await expect(getJson(fetcher, '/api/decks')).rejects.toThrow('/api/decks answered 500');
	});
});

describe.each([403, 404])('a %i from the API', (status) => {
	it.each([
		{ name: 'a read', call: (fetcher: typeof fetch) => getJson(fetcher, '/api/decks') },
		{ name: 'a write', call: (fetcher: typeof fetch) => putJson(fetcher, '/api/account', {}) },
		{ name: 'a post', call: (fetcher: typeof fetch) => postJson(fetcher, '/api/sources', {}) }
	])('becomes a kit error with its status for $name', async ({ call }) => {
		const refusal = await call(answering(status, {}).fetcher).catch((failure: unknown) => failure);

		expect(isHttpError(refusal, status)).toBe(true);
	});
});

describe('putJson', () => {
	it('sends the body as JSON with the PUT verb and returns the answer', async () => {
		const { fetcher, asked } = answering(200, { theme: 'dark' });

		await expect(putJson(fetcher, '/api/account', { theme: 'dark' })).resolves.toEqual({
			theme: 'dark'
		});

		expect(asked[0]?.method).toBe('PUT');
		expect(asked[0]?.headers.get('content-type')).toBe('application/json');
		await expect(asked[0]?.json()).resolves.toEqual({ theme: 'dark' });
	});

	it('fails loud on a refusal that is not a 401', async () => {
		const { fetcher } = answering(422, {});
		await expect(putJson(fetcher, '/api/account', {})).rejects.toThrow('/api/account answered 422');
	});
});

// The wire's own tag of a refused source, read from the document the types are generated from.
interface Document {
	paths: {
		'/api/sources': {
			post: {
				responses: {
					422: {
						content: {
							'application/problem+json': { schema: { properties: { type: { const: string } } } };
						};
					};
				};
			};
		};
	};
}
const refusedType = (
	JSON.parse(readFileSync(join(import.meta.dirname, 'openapi.json'), 'utf8')) as Document
).paths['/api/sources'].post.responses[422].content['application/problem+json'].schema.properties
	.type.const;

describe('postJson', () => {
	it('sends the body as JSON with the POST verb and returns the answer', async () => {
		const { fetcher, asked } = answering(201, { name: 'talks' });

		await expect(postJson(fetcher, '/api/sources', { name: 'talks' })).resolves.toEqual({
			name: 'talks'
		});

		expect(asked[0]?.method).toBe('POST');
		expect(asked[0]?.headers.get('accept')).toBe('application/json');
		await expect(asked[0]?.json()).resolves.toEqual({ name: 'talks' });
	});

	it('sends no body when it is given none', async () => {
		const { fetcher, asked } = answering(200, {});

		await postJson(fetcher, '/api/sources/new/key-draft', undefined);

		await expect(asked[0]?.text()).resolves.toBe('');
	});

	it('raises Refused with the reason when a source is refused with a 422', async () => {
		const { fetcher } = answering(422, { type: refusedType, reason: 'duplicate-name', title: 't' });

		const refusal = await postJson(fetcher, '/api/sources', {}).catch((failure: unknown) => failure);

		expect(refusal).toBeInstanceOf(Refused);
		expect((refusal as Refused).reason).toBe('duplicate-name');
	});

	it('fails loud on a 422 that is no refused source', async () => {
		const { fetcher } = answering(422, { detail: [] });

		await expect(postJson(fetcher, '/api/sources', {})).rejects.toThrow(
			'/api/sources answered 422'
		);
	});

	it('fails loud on a failure that is no refusal', async () => {
		await expect(postJson(answering(500, {}).fetcher, '/api/sources', {})).rejects.toThrow(
			'/api/sources answered 500'
		);
	});
});

type Client = typeof import('#lib/api/client.ts');

// Each test starts from a client that has not met a 401 yet.
async function freshClient(): Promise<Client> {
	vi.resetModules();
	return import('#lib/api/client.ts');
}

describe('a 401 before the session said where sign-in starts', () => {
	it('fails loud instead of navigating', async () => {
		const client = await freshClient();
		const navigated = vi.spyOn(client.documentNavigation, 'to').mockImplementation(() => undefined);

		await expect(client.getJson(answering(401, {}).fetcher, '/api/decks')).rejects.toThrow(
			'a 401 came before the session said where sign-in starts'
		);
		expect(navigated).not.toHaveBeenCalled();
	});
});

describe('two calls that meet a 401 together', () => {
	it('leave for the sign-in once', async () => {
		const client = await freshClient();
		const navigated = vi.spyOn(client.documentNavigation, 'to').mockImplementation(() => undefined);
		client.signInStartsAt('/login/oidc');
		window.history.replaceState(null, '', '/together');

		void client.getJson(answering(401, {}).fetcher, '/api/session');
		void client.getJson(answering(401, {}).fetcher, '/api/decks');
		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));
		await Promise.resolve();

		expect(navigated).toHaveBeenCalledTimes(1);
	});
});

describe('a page restored from the back/forward cache', () => {
	it('leaves for the sign-in again on its next 401', async () => {
		const client = await freshClient();
		const navigated = vi.spyOn(client.documentNavigation, 'to').mockImplementation(() => undefined);
		client.signInStartsAt('/login/oidc');
		window.history.replaceState(null, '', '/restored');
		void client.getJson(answering(401, {}).fetcher, '/api/decks');
		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));

		window.dispatchEvent(new Event('pagehide'));
		void client.getJson(answering(401, {}).fetcher, '/api/decks');

		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(2));
	});
});

describe.each([
	{
		name: 'a read',
		page: '/read',
		call: (client: Client, fetcher: typeof fetch) => client.getJson(fetcher, '/api/decks')
	},
	{
		name: 'a write',
		page: '/write',
		call: (client: Client, fetcher: typeof fetch) => client.putJson(fetcher, '/api/account', {})
	},
	{
		name: 'a post',
		page: '/post',
		call: (client: Client, fetcher: typeof fetch) => client.postJson(fetcher, '/api/sources', {})
	}
])('$name that meets a 401', ({ page, call }) => {
	it('leaves for the sign-in with the current page as next and never settles', async () => {
		const client = await freshClient();
		const navigated = vi.spyOn(client.documentNavigation, 'to').mockImplementation(() => undefined);
		client.signInStartsAt('/login/oidc');
		window.history.replaceState(null, '', `${page}?filter=a b`);
		const settled = vi.fn();

		void call(client, answering(401, {}).fetcher).then(settled, settled);
		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));
		await Promise.resolve();

		expect(navigated).toHaveBeenCalledWith(
			`/login/oidc?next=${encodeURIComponent(`${page}?filter=a%20b`)}`
		);
		expect(settled).not.toHaveBeenCalled();
	});
});

describe('a 401 met by a load', () => {
	it('names the load event url as next, not the page shown', async () => {
		const client = await freshClient();
		const navigated = vi.spyOn(client.documentNavigation, 'to').mockImplementation(() => undefined);
		client.signInStartsAt('/login/oidc');
		window.history.replaceState(null, '', '/shown');

		void client.getJson(
			answering(401, {}).fetcher,
			'/api/decks',
			new URL('http://lobby.test/target?a=1')
		);
		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));

		expect(navigated).toHaveBeenCalledWith(
			`/login/oidc?next=${encodeURIComponent('/target?a=1')}`
		);
	});
});
