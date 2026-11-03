import { afterEach, describe, expect, it, vi } from 'vitest';

import { documentNavigation, getJson, putJson, signInStartsAt } from '#lib/api/client.ts';

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

describe.each([
	{ name: 'a read', call: (fetcher: typeof fetch) => getJson(fetcher, '/api/decks') },
	{ name: 'a write', call: (fetcher: typeof fetch) => putJson(fetcher, '/api/account', {}) }
])('$name that meets a 401', ({ call }) => {
	it('leaves for the sign-in with the current page as next and never settles', async () => {
		const navigated = vi.spyOn(documentNavigation, 'to').mockImplementation(() => undefined);
		signInStartsAt('/login/oidc');
		window.history.replaceState(null, '', '/some/page?filter=a b');
		const settled = vi.fn();

		void call(answering(401, {}).fetcher).then(settled, settled);
		await vi.waitFor(() => expect(navigated).toHaveBeenCalledTimes(1));
		await new Promise((resolve) => setTimeout(resolve, 20));

		expect(navigated).toHaveBeenCalledWith('/login/oidc?next=%2Fsome%2Fpage%3Ffilter%3Da%2520b');
		expect(settled).not.toHaveBeenCalled();
	});
});
